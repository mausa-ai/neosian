//! Each harness's own explicit state: the exact places a headless turn
//! writes, as the investigation found them sufficient for complete turns
//! (record/investigation-2026-11-21/sandbox.md, section 1.3). Credentials,
//! the keychain and every file that is also configuration (`~/.claude.json`,
//! `~/.claude/plugins`, `~/.codex/auth.json`, `~/.codex/config.toml`,
//! OpenCode's `auth.json` and configuration) are left out: a child whose
//! sign-in needs refreshing fails and is told to sign in outside.

use std::ffi::OsString;
use std::path::{Path, PathBuf};

use super::super::adapter::Agent;
use super::super::text::say;
use super::policy::{Rule, escape, resolve, text};

/// Where one harness keeps its state, and what of it a child may write.
pub struct Client {
    /// The variable that moves the state directory, as the harness reads it.
    pub var: &'static str,
    /// Below the variable's value.
    pub under_var: &'static str,
    /// Below the home directory when the variable is unset.
    pub default: &'static str,
    pub dirs: &'static [&'static str],
    pub files: &'static [&'static str],
    /// Regular expressions for the rest of a path below the state directory.
    pub patterns: &'static [&'static str],
    /// Below an allowed directory, never writable: text there steers later
    /// sessions.
    pub protect: &'static [&'static str],
    /// A variable naming the harness's own temporary directory.
    pub tmp_var: Option<&'static str>,
}

pub fn client(agent: Agent) -> Client {
    match agent {
        // Claude Code 2.1.330: a new and a resumed session completed with
        // `projects` alone and its temporary directory moved.
        Agent::ClaudeCode => Client {
            var: "CLAUDE_CONFIG_DIR",
            under_var: "",
            default: ".claude",
            dirs: &["projects"],
            files: &[],
            patterns: &[],
            protect: &["projects/[^/]+/memory(/.*)?"],
            tmp_var: Some("CLAUDE_CODE_TMPDIR"),
        },
        // codex-cli 0.169.0: complete real turns with tools wrote no other
        // place below its home.
        Agent::Codex => Client {
            var: "CODEX_HOME",
            under_var: "",
            default: ".codex",
            dirs: &[
                "sessions",
                "cache",
                "shell_snapshots",
                "tmp",
                "thread-writer-locks",
                "mcp-oauth-locks",
                "plugins/cache",
            ],
            files: &["models_cache.json", "installation_id"],
            patterns: &["[a-z_]+_[0-9]+\\.sqlite(-shm|-wal)?"],
            protect: &[],
            tmp_var: None,
        },
        // OpenCode 2.3.2 with --standalone: its database, log, snapshots
        // and shell state.
        Agent::OpenCode => Client {
            var: "XDG_DATA_HOME",
            under_var: "opencode",
            default: ".local/share/opencode",
            dirs: &["log", "snapshot", "shell"],
            files: &["opencode.db", "opencode.db-shm", "opencode.db-wal"],
            patterns: &[],
            protect: &[],
            tmp_var: None,
        },
    }
}

/// One harness's state as policy rules.
pub struct Rules {
    pub allow: Vec<Rule>,
    pub protect: Vec<Rule>,
    pub tmp_var: Option<&'static str>,
}

/// The state directory: the variable's absolute value, else below home;
/// canonical, and strictly below the canonical home either way.
pub fn root(
    agent: Agent,
    home: &Path,
    var: &dyn Fn(&str) -> Option<OsString>,
) -> Result<PathBuf, String> {
    let client = client(agent);
    let named = var(client.var)
        .map(PathBuf::from)
        .filter(|value| value.is_absolute());
    let wanted = match named {
        Some(value) => value.join(client.under_var),
        None => home.join(client.default),
    };
    resolve(&wanted)
        .filter(|root| root.starts_with(home) && root != home)
        .ok_or_else(|| outside(agent, &wanted))
}

/// Every client's own directory, where its configuration and sign-in live,
/// as the client would place it: no child writes there beyond the named
/// state entries, even when one was moved inside its directory (X4f Q5).
pub fn roots(home: &Path, var: &dyn Fn(&str) -> Option<OsString>) -> Vec<PathBuf> {
    let placed = |name: &str, under: &str, default: &str| {
        var(name)
            .map(PathBuf::from)
            .filter(|value| value.is_absolute())
            .map_or_else(|| home.join(default), |value| value.join(under))
    };
    let mut found: Vec<PathBuf> = Agent::ALL
        .into_iter()
        .map(|agent| {
            let client = client(agent);
            placed(client.var, client.under_var, client.default)
        })
        .collect();
    // OpenCode keeps its configuration apart from its data.
    found.push(placed("XDG_CONFIG_HOME", "opencode", ".config/opencode"));
    found.iter().filter_map(|root| resolve(root)).collect()
}

fn outside(agent: Agent, path: &Path) -> String {
    say(
        "sandbox_state",
        &[
            ("agent", agent.name()),
            ("path", &path.display().to_string()),
        ],
    )
}

pub fn rules(
    agent: Agent,
    home: &Path,
    var: &dyn Fn(&str) -> Option<OsString>,
) -> Result<Rules, String> {
    let client = client(agent);
    let root = root(agent, home, var)?;
    // A name below the state directory that leads anywhere but its own
    // place (a link to the state directory itself, or elsewhere) is
    // refused, never followed: configuration and sign-in files there never
    // become writable through an alias (X4e P3).
    let below = |name: &str| -> Result<String, String> {
        let wanted = root.join(name);
        let found = resolve(&wanted)
            .filter(|found| *found == wanted)
            .ok_or_else(|| outside(agent, &wanted))?;
        text(&found)
    };
    let mut allow = Vec::new();
    for dir in client.dirs {
        allow.push(Rule::Subpath(below(dir)?));
    }
    for file in client.files {
        allow.push(Rule::Literal(below(file)?));
    }
    let prefix = escape(&text(&root)?);
    for pattern in client.patterns {
        allow.push(Rule::Pattern(format!("^{prefix}/{pattern}$")));
    }
    let protect = client
        .protect
        .iter()
        .map(|pattern| Rule::Pattern(format!("^{prefix}/{pattern}$")))
        .collect();
    Ok(Rules {
        allow,
        protect,
        tmp_var: client.tmp_var,
    })
}
