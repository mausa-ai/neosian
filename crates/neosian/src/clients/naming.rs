//! The registration key `neosian` and the earlier `neosian-memory` (DESIGN
//! §40.53): every client is written under `neosian` for a new registration,
//! keeps the old key where ours stands, and is moved by the reinstall.

/// The key earlier releases wrote; ours by name wherever it stands
/// (an entry with a `url` excepted: neosian never writes one).
pub const OLD: &str = "neosian-memory";
/// The key this release writes; ours by receipt or adoption.
pub const NEW: &str = "neosian";
/// The clients the reinstall moves: the refresh's six and Claude Desktop.
pub const CLIENTS: [&str; 7] = [
    "claude-code",
    "codex",
    "opencode",
    "muse-code",
    "cursor",
    "pi",
    "claude-desktop",
];

pub fn renamed(client: &str) -> bool {
    CLIENTS.contains(&client)
}
