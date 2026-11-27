//! Closed turns and their landing (DESIGN §40.54 item 17, #447). At Stop
//! the hook closes the open span into its conversation's pending queue
//! with a state file, then lands that queue in order; a turn recorded
//! under a policy that is no longer current is held, and later turns still
//! land. A store that keeps records, local or a server, takes each turn as
//! one draft in parts by the rules of 17.4; every other store takes it as
//! one `append_turn`. `neosian record-land` lands a home's queues with the
//! same code (`verb.rs`).

pub mod answer;
pub mod changed;
pub mod document;
pub mod draft;
pub mod files;
pub mod held;
pub mod lock;
pub mod pack;
pub mod queue;
pub mod rules;
pub mod run;
pub mod seal;
pub mod sink;
pub mod state;
pub mod turn;
pub mod verb;

#[cfg(test)]
mod tests;
