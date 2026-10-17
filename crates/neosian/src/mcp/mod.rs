//! The MCP stdio server (`_foundation/mcp/server.py`, DESIGN §39.3, R1
//! slice D): the state set served verbatim to any client, the name
//! `neosian-memory`, the eight tools, every store skill as a prompt, the
//! instructions rendered as the record renders them. One session per
//! process on newline-delimited JSON-RPC, the Python SDK's answers
//! reproduced on std alone (user ruling, 2026-10-17).

pub mod cli;
mod rpc;
mod schema;
mod server;
mod tools;
