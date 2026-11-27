//! Closed turns and their landing (DESIGN §40.54 item 17, #447). At Stop
//! the hook closes the open span into its conversation's pending queue
//! with a state file. A small turn for a local store without records lands
//! inline; every other turn is landed by `neosian record-land`, which the
//! hook starts detached (`start.rs`, `spawn.rs`, `slots.rs`, `verb.rs`).
//! A queue lands in order; a turn recorded under a policy that is no
//! longer current is held, and later turns still land. A store that keeps
//! records, local or a server, takes each turn as one draft in parts by
//! the rules of 17.4; every other store takes it as one `append_turn`. A
//! destination whose answers make it wait or block has a record that keeps
//! hooks and landers from it until it resumes (`destination.rs`, `gate.rs`).

pub mod answer;
pub mod cap;
pub mod changed;
pub mod destination;
pub mod document;
pub mod draft;
pub mod files;
pub mod gate;
pub mod held;
pub mod lock;
pub mod pack;
pub mod queue;
pub mod rules;
pub mod run;
pub mod seal;
pub mod serving;
pub mod sink;
pub mod slots;
pub mod spawn;
pub mod start;
pub mod state;
pub mod turn;
pub mod verb;

#[cfg(test)]
mod tests;
