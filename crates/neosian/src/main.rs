//! neosian — the product doors as one static binary (DESIGN §39).
//!
//! The record verb, the MCP server, the state process and the operator
//! verbs arrive phase by phase (R1 to R4); the forge ships `version` and
//! `docs`, the two verbs a differential harness can already hold against
//! the Python shell.

mod cli;
mod docs;
mod version;

use std::process::ExitCode;

fn main() -> ExitCode {
    cli::run(std::env::args_os())
}
