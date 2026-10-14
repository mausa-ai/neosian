//! `neosian record` (`_foundation/record/`, DESIGN §20.9, §21.7, §33): a
//! foreign agent's hooks write and read the record. One hook payload on
//! stdin per call; `hook_event_name` says which. The prompt and the tool
//! rounds go to the spool; the stop lands the span as one turn by
//! `<agent>:<session_id>` in the conversation the session id names and
//! writes the scope's sessions document; SessionStart prints the memory
//! index, the pending note and "where we left off", which the client
//! injects as context. The mailbox's reads ride along (#360); its writes
//! (the plugins' poll and delivery events) wait for the `messages` tool.

pub mod cli;
mod context;
mod land;
mod output;
mod payload;
mod settings;
mod spool;
