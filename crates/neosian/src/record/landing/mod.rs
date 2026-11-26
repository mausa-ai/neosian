//! Closed turns and their landing (DESIGN §40.54 item 17, #447). At Stop
//! the hook closes the open span into its conversation's pending queue
//! with a state file, then lands that queue in order; a turn recorded
//! under a policy that is no longer current is held, and later turns still
//! land. Every turn goes as one `append_turn` until records arrive.

pub mod changed;
pub mod document;
pub mod draft;
pub mod files;
pub mod lock;
pub mod queue;
pub mod state;
pub mod turn;

#[cfg(test)]
mod tests;
