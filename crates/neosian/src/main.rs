//! neosian — the product doors as one static binary (DESIGN §39).
//!
//! The record verb, the MCP server, the state process and the operator
//! verbs arrive phase by phase (R1 to R4), each held against the Python
//! shell by the differential harness: `version` and `docs` from the forge,
//! the file store, `memory`, `search` and `continue` from R1.

mod assets;
mod cli;
mod conversation;
mod docs;
mod home;
mod mcp;
mod memory;
mod messaging;
mod pyfmt;
mod record;
mod repr;
mod store;
#[cfg(test)]
mod testing;
mod text;
mod version;

use std::process::ExitCode;

fn main() -> ExitCode {
    cli::run(std::env::args_os())
}
