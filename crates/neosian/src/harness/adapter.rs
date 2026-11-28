//! The harnesses, the modes and one turn's plan (§40.50 items 4, 5, 8):
//! which executable, which argv vector, which variables beside the
//! server's own. The prompt is never part of a plan; it arrives on the
//! harness's standard input.

use std::path::{Path, PathBuf};

use super::events::Event;
use super::{claude, codex, opencode};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Agent {
    OpenCode,
    Codex,
    ClaudeCode,
}

impl Agent {
    pub const ALL: [Self; 3] = [Self::OpenCode, Self::Codex, Self::ClaudeCode];
    pub const NAMES: [&'static str; 3] = ["opencode", "codex", "claude-code"];

    pub fn parse(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|agent| agent.name() == name)
    }

    /// The tool's name for it, which is also the record verb's `--agent`.
    pub fn name(self) -> &'static str {
        match self {
            Self::OpenCode => "opencode",
            Self::Codex => "codex",
            Self::ClaudeCode => "claude-code",
        }
    }

    /// The executable looked up on the server's PATH.
    pub fn program(self) -> &'static str {
        match self {
            Self::OpenCode => "opencode",
            Self::Codex => "codex",
            Self::ClaudeCode => "claude",
        }
    }

    /// One line of the harness's headless output, read.
    pub fn read(self, line: &str) -> Vec<Event> {
        let Ok(serde_json::Value::Object(event)) = serde_json::from_str(line) else {
            return Vec::new();
        };
        match self {
            Self::OpenCode => opencode::read(&event),
            Self::Codex => codex::read(&event),
            Self::ClaudeCode => claude::read(&event),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    ReadOnly,
    Write,
}

impl Mode {
    pub const NAMES: [&'static str; 2] = ["read-only", "write"];

    pub fn parse(name: &str) -> Option<Self> {
        match name {
            "read-only" => Some(Self::ReadOnly),
            "write" => Some(Self::Write),
            _ => None,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::ReadOnly => "read-only",
            Self::Write => "write",
        }
    }
}

/// A Codex registration key that exists, with the tools it already
/// disables and its transport (X4c).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CodexKey {
    pub key: &'static str,
    pub disabled: Vec<String>,
    pub transport: super::transport::Transport,
}

/// This neosian's own MCP server as a read-only child gets it (#431): the
/// running executable and its store selection, never a command read from a
/// file. With `mounted`, every mount is in `args` and read-only, so every
/// tool may be allowed; without (a profile selects the mounts), only the
/// tools that cannot change state are.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct McpServer {
    pub command: String,
    pub args: Vec<String>,
    pub mounted: bool,
}

/// The key the child's strict MCP configuration names this server under.
pub const SERVER_KEY: &str = "neosian";

/// The neosian tools that cannot change state, allowed by name when the
/// mounts cannot be made read-only.
pub const READ_ONLY_TOOLS: [&str; 10] = [
    "list_skills",
    "load_skill",
    "recall_turn",
    "search_history",
    "docs",
    "memory_history",
    "search_memory",
    "upkeep_review",
    "knowledge",
    "changes",
];

/// What one turn needs to be planned.
pub struct Request<'a> {
    pub agent: Agent,
    pub mode: Mode,
    pub model: Option<&'a str>,
    pub directory: &'a Path,
    /// The harness session to continue; None starts a new one.
    pub resume: Option<&'a str>,
    /// The session ID neosian chose for a new Claude Code session.
    pub new_session: &'a str,
    /// The turn's private directory (Codex's TMPDIR, holding the marker).
    pub private: &'a Path,
    /// Codex: which of our keys its configuration has.
    pub codex_keys: &'a [CodexKey],
    /// OpenCode: the `OPENCODE_CONFIG_CONTENT` the server itself carries.
    pub opencode_config: Option<&'a str>,
    /// The calling session, which the child's record hook names as parent.
    pub parent: Option<&'a str>,
    /// Read-only: this neosian's server, for Claude Code's strict
    /// configuration and Codex's entry arguments.
    pub server: Option<&'a McpServer>,
    /// Codex read-only: the other configured MCP servers, switched off.
    pub codex_off: &'a [String],
    /// Only a launch under neosian's common policy changes a preset (X4b).
    pub confined: bool,
}

/// One turn's command: argv[0] is the program, resolved by the caller.
#[derive(Debug, PartialEq, Eq)]
pub struct Plan {
    pub argv: Vec<String>,
    pub env: Vec<(String, String)>,
}

/// Why a plan cannot be made.
#[derive(Debug, PartialEq, Eq)]
pub enum Unplanned {
    OpenCodeConfig,
}

/// The variables the child's record hook reads (§40.50 amended, #430).
pub const PARENT_ENV: &str = "NEOSIAN_HARNESS_PARENT";
pub const SESSION_ENV: &str = "NEOSIAN_HARNESS_SESSION";

pub fn plan(request: &Request) -> Result<Plan, Unplanned> {
    let mut plan = match request.agent {
        Agent::ClaudeCode => claude::plan(request),
        Agent::Codex => codex::plan(request),
        Agent::OpenCode => opencode::plan(request)?,
    };
    let directory = request.directory.display().to_string();
    plan.env
        .push((super::CHILD_ENV.to_string(), "1".to_string()));
    // `opencode run` takes its project from PWD, not the working directory.
    plan.env.push(("PWD".to_string(), directory));
    if let Some(parent) = request.parent {
        plan.env.push((PARENT_ENV.to_string(), parent.to_string()));
    }
    let known = match (request.resume, request.agent) {
        (Some(session), _) => Some(session),
        (None, Agent::ClaudeCode) => Some(request.new_session),
        (None, _) => None,
    };
    if let Some(session) = known {
        plan.env
            .push((SESSION_ENV.to_string(), session.to_string()));
    }
    Ok(plan)
}

/// The harness executable on `path`, absolute PATH entries only: a
/// relative entry would resolve against the child's directory.
pub fn which(program: &str, path: Option<&std::ffi::OsStr>) -> Option<PathBuf> {
    let path = path?;
    std::env::split_paths(path)
        .filter(|entry| entry.is_absolute())
        .flat_map(|entry| {
            let plain = entry.join(program);
            let windows = ["exe", "cmd", "bat"].map(|ext| plain.with_extension(ext));
            std::iter::once(plain).chain(windows)
        })
        .find(|candidate| executable(candidate))
}

pub(super) fn executable(path: &Path) -> bool {
    if !path.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        path.metadata()
            .is_ok_and(|m| m.permissions().mode() & 0o111 != 0)
    }
    #[cfg(not(unix))]
    {
        true
    }
}

/// Why a child may not work in a directory.
#[derive(Debug, PartialEq, Eq)]
pub enum Placed {
    /// The given directory does not exist.
    Missing,
    /// The directory is neither this server's own nor listed.
    NotAllowed(PathBuf),
    /// This server's directory is not strictly below the home directory,
    /// or there is no home: a directory must be listed and named.
    NeedListed,
}

/// Where a child may work (§40.50, amended by #430 and #431): the server's
/// own directory when it lies strictly below the home directory, or one the
/// person listed, compared canonically. Anywhere else, or with no home, a
/// listed directory must be named. `given` is resolved against the
/// server's directory.
pub fn place(
    given: Option<&str>,
    server: &Path,
    home: Option<&Path>,
    listed: &[PathBuf],
) -> Result<PathBuf, Placed> {
    let canonical = |path: &Path| path.canonicalize().ok().filter(|path| path.is_dir());
    let own = canonical(server);
    let home = home
        .filter(|home| !home.as_os_str().is_empty())
        .and_then(canonical);
    let own_ok = own.as_ref().filter(|own| {
        home.as_ref()
            .is_some_and(|home| own.starts_with(home) && *own != home)
    });
    let Some(given) = given else {
        return own_ok.cloned().ok_or(Placed::NeedListed);
    };
    let path = canonical(&server.join(given)).ok_or(Placed::Missing)?;
    if own_ok == Some(&path) || listed.contains(&path) {
        return Ok(path);
    }
    Err(Placed::NotAllowed(path))
}

/// The home directory from `HOME`, None when unset or empty.
pub fn home() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .filter(|home| !home.is_empty())
        .map(PathBuf::from)
}

/// A Codex server name an override can address as a bare TOML key.
pub fn bare_key(name: &str) -> bool {
    !name.is_empty()
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "_-".contains(c))
}

/// A model name in a conservative character set (§40.50 item 8), never
/// one a harness's parser could take for an option.
pub fn model_ok(model: &str) -> bool {
    (1..=128).contains(&model.len())
        && model.starts_with(|c: char| c.is_ascii_alphanumeric())
        && model
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "._:/@#+-".contains(c))
}

/// A harness session ID neosian will put on a command line: the
/// conversation-ID grammar, starting with a letter or digit.
pub fn session_ok(session: &str) -> bool {
    crate::conversation::ids::parse_conversation_id(session).is_ok()
        && session.starts_with(|c: char| c.is_ascii_alphanumeric())
}

pub const PROMPT_BYTES: usize = 256 * 1024;

pub fn prompt_ok(prompt: &str) -> bool {
    (1..=PROMPT_BYTES).contains(&prompt.len())
}

#[cfg(test)]
mod inputs;
#[cfg(test)]
mod read_only;
#[cfg(test)]
mod tests;
