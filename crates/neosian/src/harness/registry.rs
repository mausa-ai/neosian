//! This server's children (§40.50 items 2, 6, 8, 9, amended by #430):
//! `start`, `wait`, `send`, `stop` and `list`, each bounded in time. A child
//! works only in this server's own directory or one the person listed. The
//! registry lives as long as the server; dropping it stops every running
//! turn.

use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard, PoisonError};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Map, Value, json};

use super::adapter::{self, Agent, Mode, Placed};
use super::link::Naming;
use super::render::{self, Child, View};
use super::text::say;
use super::turn::Turn;
use super::{Settings, begin, is_child};
use crate::memory::dispatch::{Memory, Outcome};
use crate::pyfmt::dumps_spaced;

const WAIT_DEFAULT: i64 = 10;
const WAIT_MAX: i64 = 30;
/// The most children kept for `list`; the oldest stopped one goes first.
const KEPT: usize = 32;
/// The largest `list` answer; the oldest children are left out past it.
const LIST_BYTES: usize = 64 * 1024;

pub struct Registry {
    pub(super) settings: Settings,
    /// This server's own directory, where children work unless one listed is named.
    pub(super) here: PathBuf,
    /// The server's store selection flags, which a read-only child's
    /// neosian server repeats.
    pub(super) selection: Vec<String>,
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

pub(super) fn required(field: &str, command: &str) -> Outcome {
    Outcome::fail(say("required", &[("field", field), ("command", command)]))
}

/// The harnesses found on this server's PATH, and those that are not.
pub(super) fn available() -> (Vec<&'static str>, Vec<&'static str>) {
    let path = std::env::var_os("PATH");
    let (found, missing): (Vec<Agent>, Vec<Agent>) = Agent::ALL
        .into_iter()
        .partition(|agent| adapter::which(agent.program(), path.as_deref()).is_some());
    (
        found.into_iter().map(Agent::name).collect(),
        missing.into_iter().map(Agent::name).collect(),
    )
}

/// Turn directories a killed watcher left behind: `neosian-harness-PID-*`
/// whose PID no longer runs.
fn remove_stale() {
    let Ok(entries) = std::fs::read_dir(std::env::temp_dir()) else {
        return;
    };
    for entry in entries.flatten() {
        let name = entry.file_name();
        let pid = name
            .to_str()
            .and_then(|name| name.strip_prefix("neosian-harness-"))
            .and_then(|rest| rest.split('-').next())
            .and_then(|pid| pid.parse::<i32>().ok());
        if let Some(pid) = pid
            && !super::tree::exists(pid)
            && entry.path().join(super::MARKER).exists()
        {
            let _ = std::fs::remove_dir_all(entry.path());
        }
    }
}

impl Registry {
    pub fn new(settings: Settings, selection: Vec<String>) -> Self {
        remove_stale();
        let here = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("/"));
        Self {
            settings,
            here,
            selection,
            children: Mutex::new(Vec::new()),
            next: Mutex::new(0),
        }
    }

    fn children(&self) -> MutexGuard<'_, Vec<Child>> {
        self.children.lock().unwrap_or_else(PoisonError::into_inner)
    }

    pub fn call(&self, memory: &Memory, arguments: &Map<String, Value>) -> Outcome {
        let naming = Naming::of(memory);
        match text(arguments, "command").unwrap_or_default() {
            "start" => self.start(memory, &naming, arguments),
            "send" => self.send(memory, &naming, arguments),
            "wait" => self.wait(memory, &naming, arguments),
            "stop" => self.stop(memory, &naming, arguments),
            _ => self.list(memory, &naming),
        }
    }

    /// Where the child may work, or the refusal.
    fn place(&self, given: Option<&str>) -> Result<PathBuf, Outcome> {
        let home = std::env::home_dir();
        adapter::place(
            given,
            &self.here,
            home.as_deref(),
            &self.settings.directories,
        )
        .map_err(|placed| {
            let shown = given.unwrap_or(".");
            Outcome::fail(match placed {
                Placed::Missing => say("bad_directory", &[("directory", shown)]),
                Placed::NotAllowed(path) => {
                    say("not_allowed", &[("directory", &path.display().to_string())])
                }
                Placed::NeedListed => say("need_directory", &[]),
            })
        })
    }

    fn start(&self, memory: &Memory, naming: &Naming, arguments: &Map<String, Value>) -> Outcome {
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
        let directory = match self.place(text(arguments, "directory")) {
            Ok(directory) => directory,
            Err(refusal) => return refusal,
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
            session: None,
            existed: false,
            turns: 0,
            stopped: false,
            current: None,
            limit_seconds: self.settings.turn_seconds,
        };
        if let Err(refusal) = begin::turn(self, memory, naming, &mut child, prompt) {
            return refusal;
        }
        let shown = render::child(memory, naming, &mut child, View::Brief);
        if children.len() >= KEPT
            && let Some(oldest) = children.iter().position(|child| child.stopped)
        {
            children.remove(oldest);
        }
        children.push(child);
        answer(&shown)
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

    fn send(&self, memory: &Memory, naming: &Naming, arguments: &Map<String, Value>) -> Outcome {
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
        if child.collided() {
            return Outcome::fail(say("session_collided", &[("child", &child.id)]));
        }
        if let Err(refusal) = begin::turn(self, memory, naming, child, prompt) {
            return refusal;
        }
        answer(&render::child(memory, naming, child, View::Brief))
    }

    fn wait(&self, memory: &Memory, naming: &Naming, arguments: &Map<String, Value>) -> Outcome {
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
        answer(&render::child(memory, naming, child, View::Read))
    }

    fn stop(&self, memory: &Memory, naming: &Naming, arguments: &Map<String, Value>) -> Outcome {
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
        answer(&render::child(memory, naming, child, View::Read))
    }

    fn list(&self, memory: &Memory, naming: &Naming) -> Outcome {
        let mut children = self.children();
        let mut listed: Vec<Value> = children
            .iter_mut()
            .map(|child| render::child(memory, naming, child, View::Listed))
            .collect();
        let (found, missing) = available();
        let mut omitted = 0;
        loop {
            let whole = json!({"children": listed, "available": found, "missing": missing, "omitted": omitted});
            // Measured on the bytes the answer sends.
            let text = dumps_spaced(&whole, false, false);
            if text.len() <= LIST_BYTES || listed.is_empty() {
                return Outcome::ok(text);
            }
            listed.remove(0);
            omitted += 1;
        }
    }
}

/// The directory, absolute, as the plan carries it.
pub(super) fn shown(path: &Path) -> String {
    path.display().to_string()
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
        }
    }
}
