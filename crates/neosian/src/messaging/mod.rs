//! The mailbox (`messaging/`, DESIGN §34): message documents as pydantic
//! writes them, the inbox listing a session may read, the delivery frame
//! the hooks inject, the annotation a search hit carries (#360), and since
//! R1 slice D the writes: `send`, the fenced transitions, the lineage
//! note a continuation declares.

pub mod cli;
pub mod cli_history;
pub mod context;
pub mod delivery;
pub mod dispatch;
pub mod history;
pub mod mailbox;
pub mod transitions;
pub mod types;
pub mod writes;
