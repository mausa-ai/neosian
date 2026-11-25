//! Spooled answers (DESIGN §40.46, amended 2026-11-17, #417, #419): a route
//! writes its answer item by item into a spool while it holds the store (the
//! file store's lock, a Postgres snapshot or connection), in memory up to
//! `MEMORY` and then, the whole answer, in a private temporary file charged
//! against `NEOSIAN_MAX_TEMP_BYTES`. The store is released when the route
//! returns, and only then is the answer sent, with its length. Every refusal
//! is known before the status line and answered as before; a slow reader
//! holds a file descriptor and disk, never the store. The bytes are exactly
//! the whole answer's.
//!
//! When no spool file can be made or written (an unwritable or full
//! temporary directory), the answer is finished in memory, as 2.16.0 held
//! it, within `NEOSIAN_MAX_RESPONSE_BYTES`; past that it is refused with
//! `value_error` naming that setting, which a size-paged retry answers. A
//! spool failing midway is read back into memory first, so the answer is
//! never cut and never a bare 500.
//!
//! An answer whose first key is known only after its second is written in
//! parts (`join`): a second spool takes the later part and is joined behind
//! the first without copying a byte. The spools of one answer share its
//! memory allowance and its temporary budget (`budget`).

mod budget;
mod file;
mod join;
#[cfg(test)]
mod tests;

use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom};

use axum::body::{Body, Bytes};
use axum::http::StatusCode;
use axum::http::header::{CONTENT_LENGTH, CONTENT_TYPE};
use axum::response::Response;
use serde_json::Value;

use self::budget::Shared;
use self::file::{Spill, Spilled};
use self::join::Part;
use super::envelope::Fail;
use crate::limits::{self, Scratch};
use crate::pyfmt::dumps;

/// The largest piece written to or read back from a spool file at once.
pub const CHUNK: usize = 64 * 1024;
/// How much of an answer stays in memory before it spills to disk.
pub const MEMORY: usize = 1 << 20;

/// Where a spooled route writes its answer.
pub struct Writer {
    held: Vec<u8>,
    file: Option<Spilled>,
    /// Set once no spool can be made or written: the answer is held in
    /// memory within this many bytes.
    memory: Option<u64>,
    /// The parts joined before what this spool holds now.
    earlier: Vec<Part>,
    /// What the whole answer holds in memory and on disk, over its spools.
    budget: Shared,
    /// Tests: spool writes fail once this many bytes are on disk.
    #[cfg(test)]
    fail_after: Option<u64>,
}

impl Writer {
    fn new() -> Self {
        Self::sharing(Shared::default())
    }

    fn sharing(budget: Shared) -> Self {
        Self {
            held: Vec::new(),
            file: None,
            memory: None,
            earlier: Vec::new(),
            budget,
            #[cfg(test)]
            fail_after: None,
        }
    }

    /// Literal answer text: keys, separators and small values.
    pub fn text(&mut self, text: &str) -> io::Result<()> {
        self.bytes(text.as_bytes())
    }

    /// One JSON value as `dumps` writes it.
    pub fn value(&mut self, value: &Value) -> io::Result<()> {
        self.encoded(dumps(value, false))
    }

    /// An already encoded item.
    pub fn encoded(&mut self, item: String) -> io::Result<()> {
        self.bytes(item.as_bytes())
    }

    /// `{"<key>":[items…],"<name>":value…}`, one item encoded at a time.
    pub fn list(
        &mut self,
        key: &str,
        items: impl IntoIterator<Item = Value>,
        rest: &[(&str, Value)],
    ) -> io::Result<()> {
        self.text(&format!("{{\"{key}\":["))?;
        for (index, item) in items.into_iter().enumerate() {
            if index > 0 {
                self.text(",")?;
            }
            self.value(&item)?;
        }
        self.text("]")?;
        for (name, value) in rest {
            self.text(&format!(",\"{name}\":"))?;
            self.value(value)?;
        }
        self.text("}")
    }

    fn bytes(&mut self, mut bytes: &[u8]) -> io::Result<()> {
        if let Some(spilled) = self.file.as_mut() {
            return match spilled.write(&mut bytes) {
                Ok(()) => Ok(()),
                Err(Spill::Refused(error)) => Err(error),
                // Recover the confirmed chunks, the failed chunk and the unsubmitted suffix.
                Err(Spill::Unavailable) => self.in_memory(bytes),
            };
        }
        let held = self.budget.held();
        if self.memory.is_none() && held + bytes.len() as u64 <= MEMORY as u64 {
            self.hold(bytes);
            return Ok(());
        }
        if self.memory.is_none() {
            match self.spill() {
                Ok(spilled) => {
                    self.budget.release(self.held.len());
                    self.held = Vec::new();
                    self.file = Some(spilled);
                    return self.bytes(bytes);
                }
                Err(Spill::Refused(error)) => return Err(error),
                Err(Spill::Unavailable) => self.memory = Some(response_bytes()?),
            }
        }
        let maximum = self.memory.expect("held in memory");
        let used = self.budget.held() + bytes.len() as u64;
        limits::check(limits::RESPONSE, used, maximum).map_err(limits::into_io)?;
        self.hold(bytes);
        Ok(())
    }

    fn hold(&mut self, bytes: &[u8]) {
        self.held.extend_from_slice(bytes);
        self.budget.hold(bytes.len());
    }

    fn spill(&self) -> Result<Spilled, Spill> {
        let mut spilled = Spilled::open(self.budget.clone())?;
        #[cfg(test)]
        {
            spilled.fail_after = self.fail_after;
        }
        spilled.write(&mut self.held.as_slice())?;
        Ok(spilled)
    }

    /// The spool failed: what it holds comes back into memory, checked
    /// against the response budget before it is read.
    fn in_memory(&mut self, remaining: &[u8]) -> io::Result<()> {
        let spilled = self.file.take().expect("a spool to read back");
        let maximum = response_bytes()?;
        self.held = spilled.recover(maximum, self.budget.held(), remaining)?;
        self.budget.hold(self.held.len());
        self.memory = Some(maximum);
        Ok(())
    }

    /// The answer, sent from memory or from its file, with its length.
    fn response(mut self) -> io::Result<Response> {
        if !self.earlier.is_empty() {
            return self.joined();
        }
        if let Some(spilled) = self.file.as_mut()
            && let Err(Spill::Unavailable) = spilled.flush()
        {
            self.in_memory(&[])?;
        }
        let builder = Response::builder()
            .status(StatusCode::OK)
            .header(CONTENT_TYPE, "application/json");
        let Some(spilled) = self.file else {
            return Ok(builder
                .body(Body::from(self.held))
                .expect("a static header and a body build"));
        };
        let length = spilled.written;
        let scratch = spilled.scratch;
        let mut file = spilled.file;
        file.seek(SeekFrom::Start(0))?;
        let body = futures_util::stream::unfold(Some((file, scratch, length)), read_back);
        Ok(builder
            .header(CONTENT_LENGTH, length)
            .body(Body::from_stream(body))
            .expect("a static header and a body build"))
    }
}

fn response_bytes() -> io::Result<u64> {
    limits::response_bytes().map_err(limits::into_io)
}

type Back = Option<(File, Scratch, u64)>;

/// The next piece of a spool file, read off the runtime's workers; the
/// staging area goes with the last piece. A spool that cannot be read back
/// ends the body short, so the connection closes, as any I/O failure does.
async fn read_back(state: Back) -> Option<(io::Result<Bytes>, Back)> {
    let (mut file, scratch, left) = state?;
    if left == 0 {
        return None;
    }
    let read = tokio::task::spawn_blocking(move || {
        let mut chunk = vec![0; CHUNK.min(left as usize)];
        let outcome = file.read_exact(&mut chunk).map(|()| chunk);
        (file, outcome)
    })
    .await;
    match read {
        Ok((file, Ok(chunk))) => {
            let left = left - chunk.len() as u64;
            Some((Ok(Bytes::from(chunk)), Some((file, scratch, left))))
        }
        _ => Some((Err(io::Error::other("spool read failed")), None)),
    }
}

/// A route body writing into a `Writer`.
pub type Produce = Box<dyn FnOnce(&mut Writer) -> Result<(), Fail> + Send>;

/// Run `produce` on the blocking pool, holding the store only while it
/// runs, then answer with what it wrote.
pub async fn respond(produce: Produce) -> Response {
    let made = tokio::task::spawn_blocking(move || {
        let mut writer = Writer::new();
        produce(&mut writer)?;
        // The route has returned, so the store is released; the spool is not.
        writer.response().map_err(Fail::from)
    })
    .await;
    match made {
        Ok(Ok(response)) => response,
        Ok(Err(fail)) => fail.response(),
        Err(_) => Fail::internal().response(),
    }
}
