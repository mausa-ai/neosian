//! The login import's local machinery (the L1b slice of the L1 plan): a
//! private snapshot of the home (`snapshot.rs`, its copy in `copy.rs` and
//! its manifest in `manifest.rs`) and the units derived from it, each with
//! the bytes it sends, their length and their digest (`units.rs`). Nothing
//! here sends anything to a host; `neosian login` does not use it yet. The
//! hidden `login-snapshot` verb (`verb.rs`) is its diagnostic door for
//! contracts.

mod copy;
mod manifest;
pub(crate) mod snapshot;
#[cfg(test)]
mod tests;
pub(crate) mod units;
mod verb;

pub use verb::{LoginSnapshot, run};
