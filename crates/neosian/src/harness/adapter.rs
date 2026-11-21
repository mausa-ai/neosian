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

/// A Codex registration key that exists, with the tools it already disables.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CodexKey {
    pub key: &'static str,
    pub disabled: Vec<String>,
}

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
    /// The child's private directory (Codex's TMPDIR).
    pub private: &'a Path,
    /// Codex: which of our keys its configuration has.
    pub codex_keys: &'a [CodexKey],
    /// OpenCode: the `OPENCODE_CONFIG_CONTENT` the server itself carries.
    pub opencode_config: Option<&'a str>,
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

pub fn plan(request: &Request) -> Result<Plan, Unplanned> {
    let mut plan = match request.agent {
        Agent::ClaudeCode => claude::plan(request),
        Agent::Codex => codex::plan(request),
        Agent::OpenCode => opencode::plan(request)?,
    };
    plan.env
        .push((super::CHILD_ENV.to_string(), "1".to_string()));
    Ok(plan)
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

/// An existing directory, canonical, or None.
pub fn directory(given: Option<&str>, here: &Path) -> Option<PathBuf> {
    let path = given.map_or_else(|| here.to_path_buf(), |given| here.join(given));
    path.canonicalize().ok().filter(|path| path.is_dir())
}

#[cfg(test)]
mod tests;
