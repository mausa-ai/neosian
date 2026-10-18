//! `neosian serve`: the state process (`_foundation/server/`, DESIGN §18,
//! §39.11): the twenty-one `/v1/` routes and the handshake at
//! `WIRE_VERSION` 6 over the file store, the bearer gate with its token
//! table and allowances, the body ceiling, on axum (#366). Every store
//! call runs under `spawn_blocking` holding the root lock, as the Python
//! door's does; the hook verbs never start the runtime. `/mcp` speaks
//! MCP over streamable HTTP when the process has mounts (slice B, #370).

mod auth;
pub mod cli;
mod codec;
mod envelope;
mod mcp;
mod paging;
mod params;
mod routes;
mod routes_conversation;
mod routes_memory;
mod routes_store;
mod sessions;
mod streamable;

use std::sync::Arc;

use crate::store::FileStore;

/// What every handler shares: the store, the token table and, with
/// mounts, the MCP surface.
pub struct Shared {
    pub store: FileStore,
    pub clients: auth::Clients,
    pub mcp: Option<mcp::Surface>,
}

pub type State = Arc<Shared>;
