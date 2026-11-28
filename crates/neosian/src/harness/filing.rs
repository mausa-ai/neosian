//! Filing a watched turn (X4a): the watcher, which runs outside any limit
//! put on the child, hands the turn it captured to the record verb itself,
//! as the child's own hooks would have, one payload per call: the prompt,
//! each completed tool call, then Stop with the answer. So the recorder's
//! rules hold unchanged: capture consent and capture off, `record pause`,
//! the destination and policy the turn is closed under, the keep limit, the
//! queue and the detached lander, records or the legacy path. The verb runs
//! with this server's store selection, `--project` the child's directory,
//! and the parent and session in the variables its hooks read.
//!
//! Only a turn the provider completed is filed, under the session the
//! watcher started or continued: a new session that already has turns or a
//! sessions document is never adopted, and a turn its own hook already
//! landed is not filed again. A turn that failed, was stopped, or passed
//! the capture's bound is reported, never made up. What happened reaches
//! the server on stderr before the exit line, marked with the turn's nonce.

use std::io::{BufRead, BufReader, Write};
use std::path::Path;

use serde_json::{Value, json};

use super::adapter::{self, Agent};
use super::capture::Captured;
use super::events::{FIELD_BYTES, head};
use super::link::{self, Naming};
use super::plan::Checked;
use super::recorder::{Failure, call};
use super::text::say;
use crate::memory::mounts::Mount;
use crate::record::payload::{PROMPT_EVENT, STOP_EVENT, TOOL_EVENT};
use crate::remote::selection::Selection;

/// The line that carries what the filing did.
pub const RECORD_LINE: &str = "neosian-harness-record";
/// The line that says filing has begun: a stop no longer prevents it.
pub const BEGUN_LINE: &str = "neosian-harness-filing";
/// Dispositions after which the rest of the turn is not offered.
const REFUSED: [&str; 3] = ["capture_paused", "capture_off", "capture_off_pending"];

fn outcome(disposition: &str) -> Value {
    json!({"disposition": disposition})
}

fn failed(error: &str) -> Value {
    json!({"disposition": "error", "error": head(error, FIELD_BYTES)})
}

/// The native session a completed turn is filed under: the one the
/// harness announced, when it is the session the watcher continued, or for
/// a new Claude Code session the ID neosian gave it. `Err` names why not.
pub fn identity(
    agent: Agent,
    resume: Option<&str>,
    new_session: &str,
    announced: Option<&str>,
) -> Result<String, &'static str> {
    let Some(announced) = announced.filter(|session| adapter::session_ok(session)) else {
        return Err("session");
    };
    let expected = match (resume, agent) {
        (Some(session), _) => Some(session),
        (None, Agent::ClaudeCode) => Some(new_session),
        (None, _) => None,
    };
    if expected.is_some_and(|session| session != announced) {
        return Err("session");
    }
    Ok(announced.to_string())
}

/// Whether the conversation a new session names already has turns or a
/// sessions document here, read with this server's selection: a session
/// that existed, or a turn the child's own hook landed meanwhile.
fn existed(plan: &Checked, native: &str) -> Result<bool, String> {
    let flag = |name: &str| {
        plan.selection
            .chunks(2)
            .find(|pair| pair[0] == name)
            .map(|pair| pair[1].as_str())
    };
    let selection = Selection::resolve_profile(
        flag("--root").map(Path::new),
        flag("--url"),
        flag("--schema"),
        flag("--profile"),
        &plan.directory,
        !plan.mounts.is_empty(),
    )?;
    let text = |error| crate::conversation::Error::from(error).text();
    let store = selection.open().map_err(text)?;
    let mounts = match selection.mounts() {
        Some(mounts) => mounts?,
        None => plan
            .mounts
            .iter()
            .filter_map(|token| Mount::parse(token).ok())
            .collect(),
    };
    let scopes: Vec<String> = mounts.into_iter().map(|mount| mount.scope).collect();
    let conversation = Naming::of_store(store.as_ref()).conversation(plan.agent.name(), native);
    // A store that cannot be read is an error here, not a session that existed.
    link::last_turn(store.as_ref(), &conversation)?;
    Ok(link::existed(&store, &scopes, &conversation))
}

/// The payload for `event`, with the session and the turn's identity.
fn payload(plan: &Checked, native: &str, event: &str, mut fields: Value) -> Value {
    fields["session_id"] = json!(native);
    fields["hook_event_name"] = json!(event);
    // The watcher's turn is one generation, as a plugin names its turns.
    fields["generation_id"] = json!(plan.nonce);
    fields
}

/// File the captured turn, or say why it is not filed. `code` is the
/// harness's exit status; `captured` is None when nothing was captured.
pub fn file(plan: &Checked, prompt: &[u8], captured: Option<Captured>, code: u8) -> Value {
    let Some(captured) = captured else {
        return outcome("gap");
    };
    if captured.over {
        return outcome("bound");
    }
    if let Some(error) = &captured.error {
        return failed(error);
    }
    if !captured.completed || code != 0 {
        return outcome("gap");
    }
    // The destination the server resolved, still (X4f Q1).
    let drift =
        super::selection::drifted(&plan.selection, plan.identity.as_deref(), &plan.directory);
    if let Err(sentence) = drift {
        return failed(&sentence);
    }
    let resume = plan.resume.as_deref();
    let announced = captured.session.as_deref();
    let native = match identity(plan.agent, resume, &plan.new_session, announced) {
        Ok(native) => native,
        Err(reason) => return outcome(reason),
    };
    if resume.is_none() {
        match existed(plan, &native) {
            Ok(false) => {}
            Ok(true) => return outcome("existing"),
            Err(error) => return failed(&error),
        }
    }
    let prompt = String::from_utf8_lossy(prompt);
    let asked = payload(plan, &native, PROMPT_EVENT, json!({"prompt": prompt}));
    // A hook that fails leaves the next event to try, and so does this; a
    // verb that does not finish is not asked again.
    match call(plan, &native, &asked) {
        Ok(envelope) => {
            if let Some(refused) = envelope["disposition"]
                .as_str()
                .filter(|disposition| REFUSED.contains(disposition))
            {
                return outcome(refused);
            }
        }
        Err(Failure::Slow(error)) => return failed(&error),
        Err(Failure::Failed(_)) => {}
    }
    for line in captured
        .tools
        .into_iter()
        .flat_map(|file| BufReader::new(file).lines())
    {
        let Some(tool) = line
            .ok()
            .and_then(|line| serde_json::from_str::<Value>(&line).ok())
        else {
            return failed(&say("record_spool_unreadable", &[]));
        };
        let tool = payload(plan, &native, TOOL_EVENT, tool);
        if let Err(Failure::Slow(error)) = call(plan, &native, &tool) {
            return failed(&error);
        }
    }
    let answer = json!({"last_assistant_message": captured.answer});
    let stopped = payload(plan, &native, STOP_EVENT, answer);
    match call(plan, &native, &stopped) {
        Ok(envelope) => json!({
            "disposition": envelope["disposition"],
            "conversation": envelope["conversation_id"],
            "turn": envelope["turn"],
        }),
        Err(failure) => failed(failure.text()),
    }
}

/// Tell the server, on stderr, that filing begins (X4e P6).
pub fn begun(nonce: &str) {
    let _ = writeln!(super::ending::raw(2), "\n{BEGUN_LINE} {nonce}");
}

/// The server's side: whether the text holds the turn's begun line, and
/// the text without it.
pub fn began(text: &str, nonce: &str) -> Option<String> {
    let marker = format!("{BEGUN_LINE} {nonce}");
    text.contains(&marker).then(|| {
        text.replace(&format!("\n{marker}\n"), "\n")
            .replace(&marker, "")
    })
}

/// Write what the filing did on stderr, before the exit line.
pub fn report(nonce: &str, filed: &Value) {
    let _ = writeln!(super::ending::raw(2), "\n{RECORD_LINE} {nonce} {filed}");
}

/// The server's side: the filing's line taken out of the watcher's stderr
/// text (only the turn's nonce counts), and what it said.
pub fn taken(text: &str, nonce: &str) -> (String, Option<Value>) {
    let marker = format!("{RECORD_LINE} {nonce} ");
    let Some(at) = text.rfind(&marker) else {
        return (text.to_string(), None);
    };
    let rest = &text[at + marker.len()..];
    let end = rest.find('\n').unwrap_or(rest.len());
    let filed = serde_json::from_str::<Value>(&rest[..end]).ok();
    let mut kept = text[..at].trim_end().to_string();
    let after = rest[end..].trim();
    if !after.is_empty() {
        kept.push('\n');
        kept.push_str(after);
    }
    (kept, filed)
}

#[cfg(test)]
mod tests;
