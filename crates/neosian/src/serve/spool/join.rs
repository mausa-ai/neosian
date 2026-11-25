//! An answer in parts (DESIGN §40.54 item 10): `read_records` answers
//! `turns` before `records` although a store hands each turn's header with
//! its records, so the records go into a second spool while the headers go
//! into the first, and the second is joined behind the first. Nothing is
//! copied: a part in memory is sent as it is and a part on disk is read
//! back from its own file, so the answer costs what its parts cost, never
//! twice. The two spools share the answer's budget: together they hold at
//! most `MEMORY` in memory before one spills, and together their files are
//! charged against `NEOSIAN_MAX_TEMP_BYTES`.

use std::fs::File;
use std::io::{self, Seek, SeekFrom};

use axum::body::{Body, Bytes};
use axum::http::StatusCode;
use axum::http::header::{CONTENT_LENGTH, CONTENT_TYPE};
use axum::response::Response;
use futures_util::StreamExt;
use futures_util::future::ready;
use futures_util::stream::{iter, once, unfold};

use super::file::Spill;
use super::{Writer, read_back};
use crate::limits::Scratch;

/// A finished part of an answer: bytes in memory, or a spool file read
/// from its start with its staging area and its length.
pub(super) enum Part {
    Memory(Vec<u8>),
    File(File, Scratch, u64),
}

impl Part {
    fn len(&self) -> u64 {
        match self {
            Self::Memory(held) => held.len() as u64,
            Self::File(_, _, length) => *length,
        }
    }
}

impl Writer {
    /// A second spool of the same answer, for a part that follows bytes
    /// not yet written; it shares this spool's budget.
    pub fn scratch(&self) -> Self {
        Self::sharing(self.budget.clone())
    }

    /// What this spool holds becomes a finished part.
    fn close(&mut self) -> io::Result<Part> {
        if let Some(spilled) = self.file.as_mut()
            && let Err(Spill::Unavailable) = spilled.flush()
        {
            self.in_memory(&[])?;
        }
        let Some(spilled) = self.file.take() else {
            return Ok(Part::Memory(std::mem::take(&mut self.held)));
        };
        let mut file = spilled.file;
        file.seek(SeekFrom::Start(0))?;
        Ok(Part::File(file, spilled.scratch, spilled.written))
    }

    /// Everything `other`, a spool made by `scratch`, holds follows
    /// everything this spool holds, and what is written here next follows
    /// both. Where no spool file can be written, both go on in memory
    /// within the one response budget they share.
    pub fn join(&mut self, mut other: Writer) -> io::Result<()> {
        debug_assert!(std::sync::Arc::ptr_eq(&self.budget, &other.budget));
        let mine = self.close()?;
        let theirs = other.close()?;
        self.memory = self.memory.or(other.memory);
        self.earlier.push(mine);
        self.earlier.append(&mut other.earlier);
        self.earlier.push(theirs);
        Ok(())
    }

    /// The joined answer: each part in order, with the length of all.
    pub(super) fn joined(mut self) -> io::Result<Response> {
        let last = self.close()?;
        let mut parts = self.earlier;
        parts.push(last);
        let length: u64 = parts.iter().map(Part::len).sum();
        let body = iter(parts)
            .filter(|part| ready(part.len() > 0))
            .flat_map(|part| match part {
                Part::Memory(held) => once(ready(Ok(Bytes::from(held)))).left_stream(),
                Part::File(file, scratch, length) => {
                    unfold(Some((file, scratch, length)), read_back).right_stream()
                }
            });
        Ok(Response::builder()
            .status(StatusCode::OK)
            .header(CONTENT_TYPE, "application/json")
            .header(CONTENT_LENGTH, length)
            .body(Body::from_stream(body))
            .expect("a static header and a body build"))
    }
}

#[cfg(test)]
impl Writer {
    /// Tests: for each part joined so far, whether it is on disk.
    pub fn on_disk(&self) -> Vec<bool> {
        let disk = |part: &Part| matches!(part, Part::File(..));
        self.earlier.iter().map(disk).collect()
    }

    /// Tests: a spool of an answer of its own, as `respond` makes one.
    pub fn alone() -> Self {
        Self::new()
    }

    /// Tests: how many bytes of this answer are held in memory.
    pub fn in_memory_bytes(&self) -> u64 {
        self.budget.held()
    }

    /// Tests: the answer's declared length and its bytes as sent.
    pub fn sent(self) -> (Option<String>, Vec<u8>) {
        let response = self.response().unwrap();
        let length = response.headers().get(CONTENT_LENGTH);
        let length = length.map(|value| value.to_str().unwrap().to_string());
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        let body = runtime
            .block_on(axum::body::to_bytes(response.into_body(), usize::MAX))
            .unwrap();
        (length, body.to_vec())
    }
}

#[cfg(test)]
#[path = "join_tests.rs"]
mod tests;
