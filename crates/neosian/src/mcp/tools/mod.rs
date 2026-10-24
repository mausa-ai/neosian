//! The state set's tools (`mcp/server.py`'s `served_tools`): each a
//! declaration (the name, the prose from the prompt pack, the schema's
//! fields, the annotations) and a body over the server's memory. Listed
//! in the Python server's order: `memory`, the skills, `messages`, the
//! history pair, the handoff pair, then docs, history metadata and memory search.

mod handoff;
mod history;
mod memory;
mod messages;
mod operator;
mod search_memory;
mod skills;
mod upkeep;

use serde_json::{Map, Value};

use super::schema::Field;
use super::server::Server;
use crate::conversation::sessions::sessions_mount;
use crate::memory::dispatch::Memory;

/// What a tool answers: `ToolResult`'s data or error, and the reminder.
pub struct Outcome {
    pub result: Result<String, String>,
    pub reminder: Option<String>,
}

impl Outcome {
    pub fn ok(data: impl Into<String>) -> Self {
        Self {
            result: Ok(data.into()),
            reminder: None,
        }
    }

    pub fn fail(error: impl Into<String>) -> Self {
        Self {
            result: Err(error.into()),
            reminder: None,
        }
    }

    pub fn hint(mut self, reminder: impl Into<String>) -> Self {
        self.reminder = Some(reminder.into());
        self
    }

    /// `Tool '<name>' failed: <error>`: the execution-failure text of
    /// `agent/tool_exec.py`, for a store the body could not read.
    pub fn failed(name: &str, error: &str) -> Self {
        Self::fail(format!("Tool '{name}' failed: {error}"))
    }
}

impl From<crate::memory::dispatch::Outcome> for Outcome {
    fn from(outcome: crate::memory::dispatch::Outcome) -> Self {
        Self {
            result: outcome.result,
            reminder: outcome.reminder,
        }
    }
}

pub type Body = fn(&Server, &Map<String, Value>) -> Outcome;

/// A tool as the server declares it.
pub struct Tool {
    pub name: &'static str,
    pub description: String,
    pub params: Vec<(String, String)>,
    pub fields: &'static [Field],
    pub read_only: bool,
    pub destructive: bool,
    pub call: Body,
}

/// The tools served over `memory`'s mounts: the handoff pair only where a
/// read-write mount can hold the sessions document.
pub fn served(memory: &Memory) -> Vec<Tool> {
    let mut tools = vec![
        memory::tool(),
        skills::list(),
        skills::load(),
        messages::tool(),
    ];
    let history = memory.store.profile().is_none_or(|p| p.history.is_some());
    if history {
        tools.push(history::recall());
        tools.push(history::search());
    }
    if let Some(profile) = memory.store.profile() {
        if profile.history.is_some() {
            let mut continuation = handoff::continue_session();
            continuation.read_only =
                profile.capture.mode == crate::profile::model::CaptureMode::Off;
            tools.push(continuation);
            if !tools.last().expect("continuation").read_only {
                tools.push(handoff::handoff());
            }
        }
    } else if sessions_mount(&memory.mounts).is_some() {
        tools.push(handoff::continue_session());
        tools.push(handoff::handoff());
    }
    tools.push(operator::docs());
    tools.push(operator::history());
    tools.push(search_memory::tool());
    tools.push(upkeep::review());
    tools.push(upkeep::apply());
    tools
}

/// A string argument, present and non-null.
pub fn text<'a>(arguments: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    arguments.get(key).and_then(Value::as_str)
}

pub fn integer(arguments: &Map<String, Value>, key: &str) -> Option<i64> {
    arguments.get(key).and_then(Value::as_i64)
}
