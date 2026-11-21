//! Cross-harness sessions (DESIGN §40.50, ledger #429): on an agent's
//! explicit call, and only when this machine's configuration enables it,
//! the stdio MCP server starts one headless run of another installed
//! harness (Claude Code, Codex, OpenCode) per turn, follows its output and
//! records the link between the two sessions. Neosian holds no model key,
//! chooses no provider and runs no loop: the child harness does all of it
//! with its own login, model and permissions.

pub mod adapter;
#[cfg(unix)]
mod begin;
mod claude;
mod codex;
mod config;
#[cfg(unix)]
mod ending;
mod events;
mod link;
mod opencode;
pub mod text;

#[cfg(unix)]
pub mod plan;
#[cfg(unix)]
mod probe;
#[cfg(unix)]
pub mod registry;
#[cfg(unix)]
mod render;
#[cfg(unix)]
mod tree;
#[cfg(unix)]
mod turn;
#[cfg(unix)]
pub mod watch;

use std::env;
use std::path::Path;

pub use config::{Settings, load};

/// The variable every child carries; a server that sees it is a child.
pub const CHILD_ENV: &str = "NEOSIAN_HARNESS_CHILD";
/// The marker file in a child's private directory, which is Codex's
/// `TMPDIR`: Codex passes MCP servers TMPDIR, never the process environment.
pub const MARKER: &str = ".neosian-harness-child";

/// Whether this process is a harness child (§40.50 item 7): the
/// variable's presence is the marker, whatever its value (#430).
pub fn is_child() -> bool {
    if env::var_os(CHILD_ENV).is_some() {
        return true;
    }
    env::var_os("TMPDIR").is_some_and(|dir| Path::new(&dir).join(MARKER).exists())
}

/// The settings when the tool is served by this process: enabled, not a
/// child, on Unix. `warn` receives the reason a broken setting is ignored.
pub fn served(warn: &mut dyn FnMut(&str)) -> Option<Settings> {
    if cfg!(not(unix)) || is_child() {
        return None;
    }
    let home = crate::home::home().ok()?;
    match load(&home.join("config.toml")) {
        Ok(settings) => settings,
        Err(message) => {
            warn(&message);
            None
        }
    }
}
