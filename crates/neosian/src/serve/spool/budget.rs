//! What one answer may hold, shared by its spools (DESIGN §40.46 and
//! §40.54 item 16.1): the bytes its parts keep in memory, which together
//! stay within `MEMORY` before a spool spills, and the bytes its spool
//! files were given, which together are charged against
//! `NEOSIAN_MAX_TEMP_BYTES`. An answer with one spool counts as before.

use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};

#[derive(Default)]
pub(super) struct Budget {
    held: AtomicU64,
    disk: AtomicU64,
    /// Tests: the temporary budget of this answer, when not zero.
    #[cfg(test)]
    pub(super) temp: AtomicU64,
}

pub(super) type Shared = Arc<Budget>;

impl Budget {
    /// Bytes of the answer in memory, over every spool and joined part.
    pub(super) fn held(&self) -> u64 {
        self.held.load(Ordering::Relaxed)
    }

    pub(super) fn hold(&self, bytes: usize) {
        self.held.fetch_add(bytes as u64, Ordering::Relaxed);
    }

    pub(super) fn release(&self, bytes: usize) {
        self.held.fetch_sub(bytes as u64, Ordering::Relaxed);
    }

    /// Bytes of the answer given to spool files, over every spool.
    pub(super) fn disk(&self) -> u64 {
        self.disk.load(Ordering::Relaxed)
    }

    pub(super) fn charge(&self, bytes: u64) {
        self.disk.fetch_add(bytes, Ordering::Relaxed);
    }

    pub(super) fn refund(&self, bytes: u64) {
        self.disk.fetch_sub(bytes, Ordering::Relaxed);
    }

    /// The temporary budget the answer's spool files share.
    pub(super) fn temp_bytes(&self) -> Result<u64, crate::store::error::Error> {
        #[cfg(test)]
        if self.temp.load(Ordering::Relaxed) > 0 {
            return Ok(self.temp.load(Ordering::Relaxed));
        }
        crate::limits::temp_bytes()
    }
}
