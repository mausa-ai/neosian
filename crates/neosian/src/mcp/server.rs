//! The state set served (`mcp/server.py`): the tools in the order the
//! Python server lists them, each a name, a description, a schema and
//! its annotations; every valid skill as a prompt, listed live; a call
//! validated against the one schema, then answered as `ToolResult` in
//! MCP's vocabulary (text content, in-band `isError`, the reminder as a
//! second block).

use serde_json::{Map, Value, json};

use super::rpc::{Answer, Handler, INVALID_PARAMS, negotiate};
use super::schema::{input_schema, validate};
use super::tools::{Outcome, Tool, served};
use crate::memory::dispatch::Memory;
use crate::memory::skill::{Skill, list_skills, load_skill, shipped_skills};
use crate::version;

pub const SERVER_NAME: &str = "neosian-memory";

/// What the server holds for its one session.
pub struct Server {
    pub memory: Memory,
    pub instructions: String,
    pub shipped: Vec<Skill>,
    tools: Vec<Tool>,
}

impl Server {
    pub fn new(memory: Memory, instructions: String) -> Self {
        let tools = served(&memory);
        Self {
            memory,
            instructions,
            shipped: shipped_skills(),
            tools,
        }
    }

    fn listed(&self) -> Value {
        let tools: Vec<Value> = self
            .tools
            .iter()
            .map(|tool| {
                json!({
                    "annotations": {
                        "destructiveHint": tool.destructive,
                        "idempotentHint": tool.read_only,
                        "openWorldHint": false,
                        "readOnlyHint": tool.read_only,
                    },
                    "description": tool.description,
                    "inputSchema": input_schema(tool.fields, &tool.params),
                    "name": tool.name,
                })
            })
            .collect();
        json!({"tools": tools})
    }

    fn call(&self, params: Option<&Map<String, Value>>) -> Answer {
        let Some(name) = params
            .and_then(|params| params.get("name"))
            .and_then(Value::as_str)
        else {
            return Answer::invalid_params();
        };
        let arguments = match params.and_then(|params| params.get("arguments")) {
            None | Some(Value::Null) => Map::new(),
            Some(Value::Object(arguments)) => arguments.clone(),
            Some(_) => return Answer::invalid_params(),
        };
        let Some(tool) = self.tools.iter().find(|tool| tool.name == name) else {
            return result(Outcome::fail(format!("Tool '{name}' not found")));
        };
        let outcome = match validate(tool.fields, &arguments) {
            Ok(validated) => (tool.call)(self, &validated),
            Err(message) => {
                Outcome::fail(format!("Invalid arguments for tool '{name}': {message}"))
            }
        };
        result(outcome)
    }

    fn prompts(&self) -> Answer {
        match list_skills(&self.memory.store, &self.memory.mounts, &self.shipped) {
            Ok(entries) => {
                let prompts: Vec<Value> = entries
                    .iter()
                    .filter_map(|entry| entry.skill.as_ref())
                    .map(|skill| json!({"description": skill.description, "name": skill.name}))
                    .collect();
                Answer::Result(json!({"prompts": prompts}))
            }
            Err(error) => internal(&error.text()),
        }
    }

    fn prompt(&self, params: Option<&Map<String, Value>>) -> Answer {
        let Some(name) = params
            .and_then(|params| params.get("name"))
            .and_then(Value::as_str)
        else {
            return Answer::invalid_params();
        };
        let found = load_skill(&self.memory.store, &self.memory.mounts, &self.shipped, name);
        match found {
            Ok(Some(entry)) if entry.skill.is_some() => {
                let skill = entry.skill.expect("checked");
                Answer::Result(json!({
                    "description": skill.description,
                    "messages": [{"content": {"text": skill.content, "type": "text"}, "role": "user"}],
                }))
            }
            Ok(_) => Answer::Error {
                code: INVALID_PARAMS,
                message: format!("Skill '{name}' not found"),
                data: None,
            },
            Err(error) => internal(&error.text()),
        }
    }
}

/// A `ToolResult` in MCP's vocabulary (ledger #52).
fn result(outcome: Outcome) -> Answer {
    let (text, is_error) = match &outcome.result {
        Ok(text) => (text.clone(), false),
        Err(text) => (text.clone(), true),
    };
    let mut content = vec![json!({"text": text, "type": "text"})];
    if let Some(reminder) = &outcome.reminder {
        content.push(json!({"text": reminder, "type": "text"}));
    }
    Answer::Result(json!({"content": content, "isError": is_error}))
}

/// A failure of the store under a listing: the SDK's internal error.
fn internal(text: &str) -> Answer {
    Answer::Error {
        code: -32603,
        message: text.to_string(),
        data: None,
    }
}

impl Handler for Server {
    fn handle(&mut self, method: &str, params: Option<&Map<String, Value>>) -> Answer {
        match method {
            "initialize" => Answer::Result(json!({
                "capabilities": {"prompts": {"listChanged": false}, "tools": {"listChanged": false}},
                "instructions": self.instructions,
                "protocolVersion": negotiate(params),
                "serverInfo": {"name": SERVER_NAME, "version": version::VERSION},
            })),
            "ping" => Answer::Result(json!({})),
            "tools/list" => Answer::Result(self.listed()),
            "tools/call" => self.call(params),
            "prompts/list" => self.prompts(),
            "prompts/get" => self.prompt(params),
            other => Answer::method_not_found(other),
        }
    }
}
