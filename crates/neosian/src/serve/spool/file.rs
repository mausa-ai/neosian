//! The half of a spool on disk (DESIGN §40.46, #419): one private file in
//! its own staging area under the server's root, written a chunk at a time,
//! charged against `NEOSIAN_MAX_TEMP_BYTES`, and read back into memory when
//! it can no longer be written.

use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom, Write};

use super::CHUNK;
use super::budget::Shared;
use crate::limits::{self, Scratch};

/// The part of an answer on disk: the private staging area (removed when
/// dropped) and its one file, `written` bytes known on disk and the rest
/// still `pending` in memory.
pub(super) struct Spilled {
    pub(super) scratch: Scratch,
    pub(super) file: File,
    pub(super) pending: Vec<u8>,
    pub(super) written: u64,
    maximum: u64,
    /// The answer's budget, and what this file was given of it.
    budget: Shared,
    charged: u64,
    #[cfg(test)]
    pub(super) fail_after: Option<u64>,
}

/// Why a spool could not take bytes: a refusal (its budget) is the
/// caller's; anything else means the answer goes on in memory.
pub(super) enum Spill {
    Refused(io::Error),
    Unavailable,
}

impl Spilled {
    pub(super) fn open(budget: Shared) -> Result<Self, Spill> {
        let maximum = budget
            .temp_bytes()
            .map_err(|error| Spill::Refused(limits::into_io(error)))?;
        let root = super::super::staging::root().map_err(|_| Spill::Unavailable)?;
        let scratch = Scratch::beside(&root).map_err(|error| match error {
            crate::store::error::Error::Io(_) => Spill::Unavailable,
            other => Spill::Refused(limits::into_io(other)),
        })?;
        let file = crate::store::fileio::private()
            .read(true)
            .write(true)
            .create_new(true)
            .open(scratch.directory.join("answer.json"))
            .map_err(|_| Spill::Unavailable)?;
        Ok(Self {
            scratch,
            file,
            pending: Vec::with_capacity(CHUNK),
            written: 0,
            maximum,
            budget,
            charged: 0,
            #[cfg(test)]
            fail_after: None,
        })
    }

    /// The whole answer so far, over every spool file it has, is charged
    /// against the temporary budget.
    pub(super) fn write(&mut self, bytes: &mut &[u8]) -> Result<(), Spill> {
        let used = self.budget.disk() + bytes.len() as u64;
        limits::check(limits::TEMP, used, self.maximum)
            .map_err(|error| Spill::Refused(limits::into_io(error)))?;
        self.budget.charge(bytes.len() as u64);
        self.charged += bytes.len() as u64;
        while !bytes.is_empty() {
            let count = bytes.len().min(CHUNK - self.pending.len());
            let (chunk, remaining) = bytes.split_at(count);
            self.pending.extend_from_slice(chunk);
            *bytes = remaining;
            if self.pending.len() == CHUNK {
                self.flush()?;
            }
        }
        Ok(())
    }

    pub(super) fn flush(&mut self) -> Result<(), Spill> {
        #[cfg(test)]
        if let Some(after) = self.fail_after
            && self.written + self.pending.len() as u64 > after
        {
            let partial = (after - self.written) as usize;
            self.file
                .write_all(&self.pending[..partial])
                .map_err(|_| Spill::Unavailable)?;
            return Err(Spill::Unavailable);
        }
        self.file
            .write_all(&self.pending)
            .map_err(|_| Spill::Unavailable)?;
        self.written += self.pending.len() as u64;
        self.pending.clear();
        Ok(())
    }

    /// What the spool holds, back in memory: the bytes known on disk read
    /// back, then those still pending. The staging area goes with it.
    /// `before` is what earlier parts of the same answer hold in memory.
    pub(super) fn recover(
        mut self,
        maximum: u64,
        before: u64,
        remaining: &[u8],
    ) -> io::Result<Vec<u8>> {
        let total = self.written + self.pending.len() as u64 + remaining.len() as u64;
        limits::check(limits::RESPONSE, before + total, maximum).map_err(limits::into_io)?;
        self.budget.refund(self.charged);
        let mut held = Vec::with_capacity(total as usize);
        self.file.seek(SeekFrom::Start(0))?;
        (&self.file).take(self.written).read_to_end(&mut held)?;
        if held.len() as u64 != self.written {
            return Err(io::Error::other("spool read back short"));
        }
        held.append(&mut self.pending);
        held.extend_from_slice(remaining);
        Ok(held)
    }
}
