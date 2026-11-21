//! What the parent receives about a child (§40.50 items 6, 8, 9): its
//! state, the new output and the answer within their bounds with the cut
//! stated, where its turns are recorded in this store, and the link.

use serde_json::{Map, Value, json};

use super::adapter::{Agent, Mode};
use super::events::{OUTPUT_BYTES, head};
use super::link::{self, Linked};
use super::text::say;
use super::turn::{End, Turn, lock};
use crate::memory::dispatch::Memory;

/// A child as the registry holds it.
pub struct Child {
    pub id: String,
    pub agent: Agent,
    pub mode: Mode,
    pub model: Option<String>,
    pub directory: std::path::PathBuf,
    pub parent: Option<String>,
    pub private: std::path::PathBuf,
    pub session: Option<String>,
    pub turns: u32,
    pub stopped: bool,
    pub current: Option<Turn>,
    /// The turn whose link is confirmed, so it is not read again.
    pub linked: u32,
    pub limit_seconds: u64,
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
            self.session = lock(&turn.shared).session.clone();
        }
    }

    fn state(&self) -> &'static str {
        if self.stopped {
            return "stopped";
        }
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
fn record(memory: &Memory, child: &mut Child, ended: bool, notes: &mut Vec<String>) -> Value {
    let Some(native) = child.session.clone() else {
        notes.push(say("record_unknown", &[]));
        return Value::Null;
    };
    let conversation = link::conversation(memory, child.agent.name(), &native);
    let before = child
        .current
        .as_ref()
        .map_or(Ok(0), |turn| turn.before.clone());
    let read = before
        .and_then(|before| link::last_turn(memory, &conversation).map(|after| (before, after)));
    let (before, after) = match read {
        Ok(pair) => pair,
        Err(error) => {
            notes.push(say("record_failed", &[("error", &error)]));
            return Value::Null;
        }
    };
    let landed = after > before;
    let value = if landed {
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
    let number = child.current.as_ref().map_or(0, |turn| turn.number);
    match &child.parent {
        None => notes.push(say("not_linked_session", &[])),
        Some(_) if after == 0 => notes.push(say("not_linked_record", &[])),
        Some(parent) if child.linked == number => {
            notes.push(say("linked", &[("session", parent)]));
        }
        Some(parent) => {
            let sentence = match link::link(memory, &conversation, parent) {
                Linked::Yes => {
                    child.linked = number;
                    say("linked", &[("session", parent)])
                }
                Linked::NoMount => say("not_linked_mount", &[]),
                Linked::Retry => say("not_linked_retry", &[]),
                Linked::Failed(error) => say("link_failed", &[("error", &error)]),
            };
            notes.push(sentence);
        }
    }
    value
}

/// The child as one JSON object; `consume` moves the read position past
/// the output it returns (wait, stop), `list` leaves it.
pub fn child(memory: &Memory, child: &mut Child, consume: bool) -> Value {
    child.learn();
    let state = child.state();
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
        .map(|native| link::conversation(memory, child.agent.name(), native));
    out.insert("conversation".into(), json!(conversation));
    let mut notes = Vec::new();
    let ended = state != "running" && state != "starting";
    if let Some(turn) = child.current.as_mut() {
        let shared = lock(&turn.shared);
        if consume {
            let (output, skipped) = shared.transcript.since(turn.read, OUTPUT_BYTES);
            out.insert("output".into(), json!(output));
            out.insert("output_skipped".into(), json!(skipped));
            if skipped > 0 {
                notes.push(say("output_cut", &[("bytes", &skipped.to_string())]));
            }
            turn.read = shared.transcript.total();
        }
        if ended && let Some(answer) = shared.final_answer() {
            let shown = head(answer, OUTPUT_BYTES);
            out.insert("answer".into(), json!(shown));
            if shown.len() < answer.len() {
                out.insert("answer_bytes".into(), json!(answer.len()));
                notes.push(say(
                    "answer_cut",
                    &[
                        ("shown", &shown.len().to_string()),
                        ("bytes", &answer.len().to_string()),
                    ],
                ));
            }
        }
        if let Some(End::Exited(code)) = shared.end.filter(|_| state == "failed") {
            out.insert("exit".into(), json!(code));
            let error = shared
                .failure
                .clone()
                .unwrap_or_else(|| shared.stderr.text().to_string());
            out.insert("error".into(), json!(error));
        }
        if state == "timed_out" {
            notes.push(say(
                "timed_out",
                &[("seconds", &child.limit_seconds.to_string())],
            ));
        }
    }
    let recorded = record(memory, child, ended, &mut notes);
    out.insert("record".into(), recorded);
    notes.push(match state {
        "running" | "starting" => say("running", &[("child", &child.id)]),
        "stopped" => say("stopped_note", &[]),
        _ => say("ended", &[("child", &child.id)]),
    });
    out.insert("notes".into(), json!(notes));
    Value::Object(out)
}
