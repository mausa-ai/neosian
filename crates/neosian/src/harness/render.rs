//! What the parent receives about a child (§40.50 items 6, 8, 9, amended
//! by #430): its state; the child's own words (`text`, `answer`) apart from
//! what neosian observed (`events`) and from neosian's own sentences
//! (`notes`); every text bounded with the cut stated; where its turns are
//! recorded in this store; and the link, said only once read back.

use serde_json::{Map, Value, json};

use super::adapter::{Agent, Mode};
use super::events::{ERROR_BYTES, EVENTS_PER_ANSWER, OUTPUT_BYTES, head};
use super::link::{self, Linked, Naming};
use super::text::say;
use super::turn::{End, Turn, lock};
use crate::memory::dispatch::Memory;

/// How much of an error `list` shows per child.
const LIST_ERROR_BYTES: usize = 512;

/// A child as the registry holds it.
pub struct Child {
    pub id: String,
    pub agent: Agent,
    pub mode: Mode,
    pub model: Option<String>,
    pub directory: std::path::PathBuf,
    pub parent: Option<String>,
    pub session: Option<String>,
    /// The session the harness announced already existed in this store.
    pub existed: bool,
    pub turns: u32,
    pub stopped: bool,
    pub current: Option<Turn>,
    pub limit_seconds: u64,
}

/// How much one answer says about a child.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum View {
    /// `wait` and `stop`: new text and events, consumed.
    Read,
    /// `start` and `send`: the state.
    Brief,
    /// `list`: the state, no text, a short error.
    Listed,
}

impl Child {
    pub fn running(&self) -> bool {
        self.current.as_ref().is_some_and(|turn| !turn.ended())
    }

    /// The harness session, learned from the current turn when it is new.
    pub fn learn(&mut self) {
        if self.session.is_none()
            && let Some(turn) = &self.current
        {
            let shared = lock(&turn.shared);
            self.session = shared.session.clone();
            self.existed = shared.existed;
        }
    }

    fn state(&self) -> &'static str {
        match (self.stopped, self.outcome()) {
            (true, _) => "stopped",
            (false, outcome) => outcome,
        }
    }

    /// How the current turn itself went, whatever became of the child.
    fn outcome(&self) -> &'static str {
        let Some(turn) = &self.current else {
            return "starting";
        };
        let shared = lock(&turn.shared);
        if !shared.ended() {
            return "running";
        }
        match shared.end {
            Some(End::Exited(0)) if shared.failure.is_none() => "finished",
            Some(End::TimedOut) => "timed_out",
            Some(End::Stopped) => "stopped",
            _ => "failed",
        }
    }
}

/// How the parent may read a turn: the conversation and the turns that
/// landed, or why there are none; and the link once the turn has ended.
fn record(
    memory: &Memory,
    naming: &Naming,
    child: &Child,
    ended: bool,
    notes: &mut Vec<String>,
) -> Value {
    let Some(native) = child.session.as_deref() else {
        notes.push(say("record_unknown", &[]));
        return Value::Null;
    };
    if child.existed {
        notes.push(say("announced_existing", &[("session", native)]));
        return Value::Null;
    }
    let conversation = naming.conversation(child.agent.name(), native);
    let before = child
        .current
        .as_ref()
        .map_or(Ok(0), |turn| turn.before.clone());
    let read = before.and_then(|before| {
        link::last_turn(memory.store.as_ref(), &conversation).map(|after| (before, after))
    });
    let (before, after) = match read {
        Ok(pair) => pair,
        Err(error) => {
            notes.push(say(
                "record_failed",
                &[("error", head(&error, ERROR_BYTES))],
            ));
            return Value::Null;
        }
    };
    let value = if after > before {
        let turns = if after == before + 1 {
            after.to_string()
        } else {
            format!("{}-{after}", before + 1)
        };
        notes.push(say(
            "recorded",
            &[("conversation", &conversation), ("turns", &turns)],
        ));
        json!({"conversation": conversation, "turns": turns})
    } else {
        notes.push(say("not_recorded", &[]));
        Value::Null
    };
    if !ended {
        return value;
    }
    let Some(parent) = child.parent.as_deref() else {
        notes.push(say("not_linked_session", &[]));
        return value;
    };
    notes.push(
        match link::link(memory, child.agent.name(), &conversation, parent) {
            Linked::Yes => say("linked", &[("session", parent)]),
            Linked::Written => say("link_written", &[("session", parent)]),
            Linked::Absent if after > 0 => say("not_linked_mount", &[]),
            Linked::Absent => say("not_linked_absent", &[]),
            Linked::NoMount => say("not_linked_mount", &[]),
            Linked::OtherAgent => say("not_linked_agent", &[]),
            Linked::Retry => say("not_linked_retry", &[]),
            Linked::Failed(error) => say("link_failed", &[("error", head(&error, ERROR_BYTES))]),
        },
    );
    value
}

/// The child as one JSON object.
pub fn child(memory: &Memory, naming: &Naming, child: &mut Child, view: View) -> Value {
    child.learn();
    let state = child.state();
    // A stopped child still says why its last turn failed.
    let failed = child.outcome() == "failed";
    let mut out = Map::new();
    out.insert("child".into(), json!(child.id));
    out.insert("agent".into(), json!(child.agent.name()));
    out.insert("mode".into(), json!(child.mode.name()));
    out.insert("model".into(), json!(child.model));
    out.insert(
        "directory".into(),
        json!(child.directory.display().to_string()),
    );
    out.insert("state".into(), json!(state));
    out.insert("turn".into(), json!(child.turns));
    out.insert("session".into(), json!(child.session));
    let conversation = child
        .session
        .as_deref()
        .filter(|_| !child.existed)
        .map(|native| naming.conversation(child.agent.name(), native));
    out.insert("conversation".into(), json!(conversation));
    let mut notes = Vec::new();
    let ended = state != "running" && state != "starting";
    if let Some(turn) = child.current.as_mut() {
        let shared = lock(&turn.shared);
        if view == View::Read {
            let (text, skipped) = shared.text.since(turn.read_text, OUTPUT_BYTES);
            // Two places are kept for the truncation entries, so the cap holds.
            let (mut events, left_out) = shared.log.since(turn.read_events, EVENTS_PER_ANSWER - 2);
            if left_out > 0 {
                events.insert(
                    0,
                    json!({"kind": "truncated", "field": "events", "count": left_out}),
                );
            }
            if skipped > 0 {
                events.insert(
                    0,
                    json!({"kind": "truncated", "field": "text", "bytes": skipped}),
                );
                notes.push(say("output_cut", &[("bytes", &skipped.to_string())]));
            }
            out.insert("text".into(), json!(text));
            out.insert("events".into(), json!(events));
            turn.read_text = shared.text.total();
            turn.read_events = shared.log.total();
        }
        if ended
            && view != View::Listed
            && let Some(answer) = shared.final_answer()
        {
            let shown = head(answer, OUTPUT_BYTES);
            out.insert("answer".into(), json!(shown));
            if shown.len() < answer.len() {
                out.insert("answer_bytes".into(), json!(answer.len()));
                let (shown, bytes) = (shown.len().to_string(), answer.len().to_string());
                notes.push(say("answer_cut", &[("shown", &shown), ("bytes", &bytes)]));
            }
        }
        if failed {
            let limit = if view == View::Listed {
                LIST_ERROR_BYTES
            } else {
                ERROR_BYTES
            };
            let error = match (&shared.failure, shared.end) {
                (Some(failure), _) => failure.clone(),
                (None, Some(End::Lost)) => say("watcher_lost", &[]),
                (None, _) => shared.stderr.text().to_string(),
            };
            out.insert("error".into(), json!(head(&error, limit)));
            if let Some(End::Exited(code)) = shared.end {
                out.insert("exit".into(), json!(code));
            }
        }
        if state == "timed_out" {
            notes.push(say(
                "timed_out",
                &[("seconds", &child.limit_seconds.to_string())],
            ));
        }
    }
    let recorded = record(memory, naming, child, ended, &mut notes);
    out.insert("record".into(), recorded);
    notes.push(match state {
        "running" | "starting" => say("running", &[("child", &child.id)]),
        "stopped" => say("stopped_note", &[]),
        _ => say("ended", &[("child", &child.id)]),
    });
    out.insert("notes".into(), json!(notes));
    Value::Object(out)
}
