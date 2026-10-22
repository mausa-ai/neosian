//! neosian — the product doors as one static binary (DESIGN §39).
//!
//! The record verb, the MCP server, the state process and the operator
//! verbs arrive phase by phase (R1 to R4), each held against the Python
//! shell by the differential harness: `version` and `docs` from the forge,
//! the file store, `memory`, `search`, `continue`, `record` and `mcp` from
//! R1, `serve` from R2, and `setup`, `status`, `configure` from R3.

mod assets;
mod cli;
mod clients;
mod config;
mod configure;
mod conversation;
mod docs;
mod home;
mod mcp;
mod memory;
mod messaging;
mod pyfmt;
mod record;
mod remote;
mod repr;
mod serve;
mod shape;
mod state;
mod store;
#[cfg(test)]
mod testing;
mod text;
mod update;
mod version;
mod wire;

use std::process::ExitCode;

fn main() -> ExitCode {
    cli::run(std::env::args_os())
}
