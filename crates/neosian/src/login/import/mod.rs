//! The login import (the L1 plan). L1b: a private snapshot of the home
//! (`snapshot.rs`, its copy in `copy.rs` and its manifest in `manifest.rs`)
//! and the units derived from it, each with the bytes it sends, their
//! length and their digest (`units.rs`); the hidden `login-snapshot` verb
//! (`verb.rs`) is its diagnostic door for contracts. L1c: the offer at
//! login (`consent.rs`), the host's session routes (`session.rs`) over
//! same-origin requests (`link.rs`), the transfer of each unit
//! (`transfer.rs`), the session from Begin to its result with its
//! recovery state (`drive.rs`, `journal.rs`), what the person is told
//! (`report.rs`) and the ways in (`entry.rs`).

mod consent;
mod copy;
mod drive;
mod entry;
mod journal;
mod link;
mod manifest;
mod report;
mod session;
pub(crate) mod snapshot;
#[cfg(test)]
mod tests;
mod transfer;
#[cfg(test)]
mod transfer_tests;
pub(crate) mod units;
mod verb;

pub(crate) use consent::{Offer, offer};
pub(crate) use entry::{Route, after_login, discard, resume, route};
pub use verb::{LoginSnapshot, run};
