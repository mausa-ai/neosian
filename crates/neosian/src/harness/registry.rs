//! This server's children (§40.50 items 2, 6, 8, 9): `start`, `wait`,
//! `send`, `stop` and `list`, each bounded in time. The registry lives as
//! long as the server; dropping it stops every running turn.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::{Mutex, MutexGuard, PoisonError};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Map, Value, json};

use super::adapter::{self, Agent, Mode, Request, Unplanned};
use super::render::{self, Child};
use super::text::say;
use super::turn::{Turn, private_dir, prompt_file};
use super::{Settings, is_child, link, probe};
use crate::clients::runner::which;
use crate::memory::dispatch::{Memory, Outcome};
use crate::pyfmt::dumps_spaced;

/// How long `start` and `send` wait for a new session's ID.
const SESSION_WAIT: Duration = Duration::from_secs(3);
const WAIT_DEFAULT: i64 = 10;
const WAIT_MAX: i64 = 30;
/// The most children kept for `list`; the oldest stopped one goes first.
const KEPT: usize = 32;

pub struct Registry {
    settings: Settings,
    here: PathBuf,
    children: Mutex<Vec<Child>>,
    next: Mutex<u32>,
}

fn text<'a>(arguments: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    arguments.get(key).and_then(Value::as_str)
}

/// One JSON object, its text unescaped so a child's words read as written.
fn answer(value: &Value) -> Outcome {
    Outcome::ok(dumps_spaced(value, false, false))
}

fn required(field: &str, command: &str) -> Outcome {
    Outcome::fail(say("required", &[("field", field), ("command", command)]))
}

/// The PATH lookup environment of this server.
fn path_env() -> BTreeMap<String, String> {
    ["PATH", "PATHEXT"]
        .into_iter()
        .filter_map(|key| {
            std::env::var(key)
                .ok()
                .map(|value| (key.to_string(), value))
        })
        .collect()
}

/// The harnesses found on this server's PATH, and those that are not.
fn available() -> (Vec<&'static str>, Vec<&'static str>) {
    let env = path_env();
    let (found, missing): (Vec<Agent>, Vec<Agent>) = Agent::ALL
        .into_iter()
        .partition(|agent| which(agent.program(), &env).is_some());
    (
        found.into_iter().map(Agent::name).collect(),
        missing.into_iter().map(Agent::name).collect(),
    )
}

impl Registry {
    pub fn new(settings: Settings) -> Self {
        Self {
            settings,
            here: std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
            children: Mutex::new(Vec::new()),
            next: Mutex::new(0),
        }
    }

    fn children(&self) -> MutexGuard<'_, Vec<Child>> {
        self.children.lock().unwrap_or_else(PoisonError::into_inner)
    }

    pub fn call(&self, memory: &Memory, arguments: &Map<String, Value>) -> Outcome {
        match text(arguments, "command").unwrap_or_default() {
            "start" => self.start(memory, arguments),
            "send" => self.send(memory, arguments),
            "wait" => self.wait(memory, arguments),
            "stop" => self.stop(memory, arguments),
            _ => self.list(memory),
        }
    }

    fn start(&self, memory: &Memory, arguments: &Map<String, Value>) -> Outcome {
        if is_child() {
            return Outcome::fail(say("child_refused", &[]));
        }
        let Some(agent) = text(arguments, "agent").and_then(Agent::parse) else {
            return required("agent", "start");
        };
        let Some(prompt) = text(arguments, "prompt") else {
            return required("prompt", "start");
        };
        if !adapter::prompt_ok(prompt) {
            return Outcome::fail(say("bad_prompt", &[]));
        }
        let mode = text(arguments, "mode")
            .and_then(Mode::parse)
            .unwrap_or(Mode::ReadOnly);
        let model = text(arguments, "model");
        if model.is_some_and(|model| !adapter::model_ok(model)) {
            return Outcome::fail(say("bad_model", &[]));
        }
        let given = text(arguments, "directory");
        let Some(directory) = adapter::directory(given, &self.here) else {
            let shown = given.unwrap_or(".");
            return Outcome::fail(say("bad_directory", &[("directory", shown)]));
        };
        let parent = text(arguments, "session");
        if let Some(parent) = parent
            && let Err(refused) = crate::conversation::ids::parse_conversation_id(parent)
        {
            return Outcome::fail(say("bad_session", &[("error", &refused.message)]));
        }
        let mut children = self.children();
        let open = children.iter().filter(|child| !child.stopped).count();
        if open >= self.settings.max_children {
            let max = self.settings.max_children.to_string();
            return Outcome::fail(say("too_many", &[("max", &max)]));
        }
        let private = match private_dir() {
            Ok(private) => private,
            Err(error) => return spawn_failed(agent, &error.to_string()),
        };
        let id = {
            let mut next = self.next.lock().unwrap_or_else(PoisonError::into_inner);
            *next += 1;
            format!("h{next}")
        };
        let mut child = Child {
            id,
            agent,
            mode,
            model: model.map(str::to_string),
            directory,
            parent: parent.map(str::to_string),
            private,
            session: None,
            turns: 0,
            stopped: false,
            current: None,
            linked: 0,
            limit_seconds: self.settings.turn_seconds,
        };
        if let Err(refusal) = self.begin(memory, &mut child, prompt) {
            let _ = std::fs::remove_dir_all(&child.private);
            return refusal;
        }
        let shown = render::child(memory, &mut child, false);
        if children.len() >= KEPT
            && let Some(oldest) = children.iter().position(|child| child.stopped)
        {
            children.remove(oldest);
        }
        children.push(child);
        answer(&shown)
    }

    /// One headless run of the child's harness with `prompt` on its stdin.
    fn begin(&self, memory: &Memory, child: &mut Child, prompt: &str) -> Result<(), Outcome> {
        let env = path_env();
        let Some(program) = which(child.agent.program(), &env) else {
            let (found, _) = available();
            let list = if found.is_empty() {
                say("none_available", &[])
            } else {
                found.join(", ")
            };
            return Err(Outcome::fail(say(
                "missing_harness",
                &[("agent", child.agent.name()), ("available", &list)],
            )));
        };
        let codex_keys = if child.agent == Agent::Codex {
            probe::codex_keys(&program, &child.directory).map_err(|(key, error)| {
                Outcome::fail(say(
                    "codex_probe_failed",
                    &[("key", &key), ("error", &error)],
                ))
            })?
        } else {
            Vec::new()
        };
        let new_session = uuid::Uuid::new_v4().to_string();
        let opencode_config = std::env::var(super::opencode::CONFIG_ENV).ok();
        let request = Request {
            agent: child.agent,
            mode: child.mode,
            model: child.model.as_deref(),
            directory: &child.directory,
            resume: child.session.as_deref(),
            new_session: &new_session,
            private: &child.private,
            codex_keys: &codex_keys,
            opencode_config: opencode_config.as_deref(),
        };
        let mut plan = adapter::plan(&request)
            .map_err(|Unplanned::OpenCodeConfig| Outcome::fail(say("bad_opencode_config", &[])))?;
        plan.argv[0] = program.display().to_string();
        // A new session has no turns; a continued one is read now, so the
        // turns that land during this one can be told apart.
        let before = match &child.session {
            Some(native) => {
                let conversation = link::conversation(memory, child.agent.name(), native);
                link::last_turn(memory, &conversation)
            }
            None => Ok(0),
        };
        let number = child.turns + 1;
        let started = prompt_file(&child.private, number, prompt).and_then(|file| {
            let limit = Duration::from_secs(self.settings.turn_seconds);
            let turn = Turn::begin(
                child.agent,
                number,
                &plan.argv,
                &plan.env,
                &child.directory,
                &file,
                limit,
            );
            if turn.is_err() {
                let _ = std::fs::remove_file(&file);
            }
            turn
        });
        let mut turn = started.map_err(|error| spawn_failed(child.agent, &error.to_string()))?;
        turn.before = before;
        if child.session.is_none() && child.agent == Agent::ClaudeCode {
            child.session = Some(new_session);
        }
        child.turns = number;
        child.current = Some(turn);
        let deadline = Instant::now() + SESSION_WAIT;
        while child.session.is_none() && Instant::now() < deadline && child.running() {
            child.learn();
            thread::sleep(Duration::from_millis(20));
        }
        Ok(())
    }

    fn find<'a>(
        children: &'a mut [Child],
        arguments: &Map<String, Value>,
        command: &str,
    ) -> Result<&'a mut Child, Outcome> {
        let Some(id) = text(arguments, "child") else {
            return Err(required("child", command));
        };
        children
            .iter_mut()
            .find(|child| child.id == id)
            .ok_or_else(|| Outcome::fail(say("unknown_child", &[("child", id)])))
    }

    fn send(&self, memory: &Memory, arguments: &Map<String, Value>) -> Outcome {
        let mut children = self.children();
        let child = match Self::find(&mut children, arguments, "send") {
            Ok(child) => child,
            Err(refusal) => return refusal,
        };
        let Some(prompt) = text(arguments, "prompt") else {
            return required("prompt", "send");
        };
        if !adapter::prompt_ok(prompt) {
            return Outcome::fail(say("bad_prompt", &[]));
        }
        if child.stopped {
            return Outcome::fail(say("stopped", &[("child", &child.id)]));
        }
        if child.running() {
            return Outcome::fail(say("busy", &[("child", &child.id)]));
        }
        child.learn();
        if child.session.is_none() {
            return Outcome::fail(say("no_session", &[("child", &child.id)]));
        }
        if let Err(refusal) = self.begin(memory, child, prompt) {
            return refusal;
        }
        answer(&render::child(memory, child, false))
    }

    fn wait(&self, memory: &Memory, arguments: &Map<String, Value>) -> Outcome {
        let seconds = arguments
            .get("seconds")
            .and_then(Value::as_i64)
            .unwrap_or(WAIT_DEFAULT);
        if !(0..=WAIT_MAX).contains(&seconds) {
            return Outcome::fail(say("bad_seconds", &[]));
        }
        let mut children = self.children();
        let child = match Self::find(&mut children, arguments, "wait") {
            Ok(child) => child,
            Err(refusal) => return refusal,
        };
        let deadline = Instant::now() + Duration::from_secs(seconds.unsigned_abs());
        while child.running() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(25));
        }
        answer(&render::child(memory, child, true))
    }

    fn stop(&self, memory: &Memory, arguments: &Map<String, Value>) -> Outcome {
        let mut children = self.children();
        let child = match Self::find(&mut children, arguments, "stop") {
            Ok(child) => child,
            Err(refusal) => return refusal,
        };
        if let Some(turn) = child.current.as_mut() {
            turn.ask_stop();
            turn.join(Turn::stop_bound());
            let drain = Instant::now() + Duration::from_secs(1);
            while !turn.ended() && Instant::now() < drain {
                thread::sleep(Duration::from_millis(20));
            }
        }
        child.stopped = true;
        let shown = render::child(memory, child, true);
        let _ = std::fs::remove_dir_all(&child.private);
        answer(&shown)
    }

    fn list(&self, memory: &Memory) -> Outcome {
        let mut children = self.children();
        let listed: Vec<Value> = children
            .iter_mut()
            .map(|child| render::child(memory, child, false))
            .collect();
        let (found, missing) = available();
        answer(&json!({"children": listed, "available": found, "missing": missing}))
    }
}

fn spawn_failed(agent: Agent, error: &str) -> Outcome {
    Outcome::fail(say(
        "spawn_failed",
        &[("agent", agent.name()), ("error", error)],
    ))
}

impl Drop for Registry {
    /// The server is ending: every running turn is stopped, within one bound.
    fn drop(&mut self) {
        let mut children = self.children();
        for child in children.iter() {
            if let Some(turn) = &child.current {
                turn.ask_stop();
            }
        }
        let deadline = Instant::now() + Turn::stop_bound();
        for child in children.iter_mut() {
            if let Some(turn) = child.current.as_mut() {
                let left = deadline.saturating_duration_since(Instant::now());
                turn.join(left);
            }
            let _ = std::fs::remove_dir_all(&child.private);
        }
    }
}
