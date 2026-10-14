//! The mailbox's read side (`messaging/`, DESIGN §34, #360): message
//! documents as pydantic writes them, the inbox listing a session may
//! read, the delivery frame the hooks inject and the annotation a search
//! hit carries. The writes (`send`, the transitions, the lineage note)
//! land with the `messages` tool at R1 slice D.

pub mod cli_history;
pub mod delivery;
pub mod mailbox;
pub mod types;
