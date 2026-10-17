//! The sessions documents and the handoff baton (`memory/sessions.py`,
//! DESIGN §20.9, §22.2, §32, §33): a session is listed as a memory
//! document `sessions/<conversation_id>` in the mount at `/project` when
//! one is present, and the mount holds one baton, `handoff`. Both
//! declarations are read back from the record itself: the tools' calls in
//! a turn, paired with the headers their results begin with, because the
//! MCP server never knows the session it serves and only the hooks do.
//! Pure vocabulary; the record verb writes the document and the link, the
//! tool's pickup lands with the MCP server.

use std::borrow::Cow;
use std::collections::HashMap;

use super::Error;
use super::message::{Message, Role, text_of};
use crate::memory::mounts::Mount;
use crate::store::FileStore;
use crate::store::error::Error as StoreError;
use crate::store::grammar::Actor;
use crate::store::time::Stamp;
use crate::text::{head, splitlines, strip};

pub const SESSIONS_PREFIX: &str = "sessions/";
pub const HANDOFF_PATH: &str = "handoff";
pub const PROJECT_MOUNT_PATH: &str = "project";
const PROMPT_LINE_CHARS: usize = 200;
const HANDOFF_FULL_DAYS: i64 = 7;
const DAY_MICROS: i64 = 86_400_000_000;
const CONTINUE_TOOL: &str = "continue_session";
const HANDOFF_TOOL: &str = "handoff";
// The headers the two tools' results begin with; a client prefixes the
// tool's name (`mcp__neosian-memory__continue_session`) and may JSON-dump
// the result, so the header is searched, never anchored.
const CONTINUE_HEADER: &str = "[continuing conversation ";
const HANDOFF_HEADER: &str = "[handoff note recorded";

pub fn sessions_path(session_id: &str) -> String {
    format!("{SESSIONS_PREFIX}{session_id}")
}

/// The scope's per-session document: the listing the next agent finds
/// in its index (memory documents, never a store method).
pub fn sessions_document(
    agent: &str,
    session_id: &str,
    started: Stamp,
    last_prompt: Option<&str>,
    turns: u64,
    continues: &[String],
) -> String {
    let first = splitlines(strip(last_prompt.unwrap_or("")))
        .into_iter()
        .next();
    let prompt = first.map_or("-", |line| head(line, PROMPT_LINE_CHARS));
    let mut text = format!(
        "# {agent} session {session_id}\n\n- agent: {agent}\n- conversation: {session_id}\n\
         - started: {}\n- last prompt: {prompt}\n- turns: {turns}\n",
        started.z()
    );
    for continued in continues {
        text += &format!("- continues: {continued}\n");
    }
    text
}

/// The `- key: value` lines of a document, in order.
fn pairs(text: &str) -> Vec<(&str, &str)> {
    splitlines(text)
        .into_iter()
        .filter_map(|line| {
            let (key, value) = line.strip_prefix("- ")?.split_once(": ")?;
            Some((key, strip(value)))
        })
        .collect()
}

/// Which sessions a sessions document says it continues, in order.
pub fn continues(content: &str) -> Vec<String> {
    pairs(content)
        .into_iter()
        .filter(|(key, _)| *key == "continues")
        .map(|(_, value)| value.to_string())
        .collect()
}

/// `_results`: the TOOL messages' texts by call id.
fn results(messages: &[Message]) -> HashMap<&str, Cow<'_, str>> {
    messages
        .iter()
        .filter(|message| message.role == Role::Tool)
        .map(|message| {
            (
                message.tool_call_id.as_deref().unwrap_or(""),
                text_of(message),
            )
        })
        .collect()
}

/// The tool, bare or under a client's prefix (`mcp__server__handoff`).
fn is_tool(name: &str, tool: &str) -> bool {
    name == tool || name.ends_with(&format!("_{tool}"))
}

/// `CONTINUE_HEADER.search(text)`: the id the first well-formed header
/// names, a run of the id grammar's characters.
fn continued_header(text: &str) -> Option<&str> {
    let mut rest = text;
    while let Some(at) = rest.find(CONTINUE_HEADER) {
        let after = &rest[at + CONTINUE_HEADER.len()..];
        let grammatical = |ch: char| ch.is_ascii_alphanumeric() || "_.-".contains(ch);
        let end = after.find(|ch| !grammatical(ch)).unwrap_or(after.len());
        if end > 0 {
            return Some(&after[..end]);
        }
        rest = after;
    }
    None
}

/// The conversations a turn declared it continues, in order: each
/// `continue_session` call whose result carries the header the tool
/// authored (the first header, since the delivered text may quote
/// another); a stranger tool quoting one never counts.
pub fn continued_ids(messages: &[Message]) -> Vec<String> {
    let results = results(messages);
    let mut found: Vec<String> = Vec::new();
    for call in messages.iter().flat_map(|message| &message.tool_calls) {
        if is_tool(&call.name, CONTINUE_TOOL)
            && let Some(result) = results.get(call.id.as_str())
            && let Some(id) = continued_header(result)
            && !found.iter().any(|known| known == id)
        {
            found.push(id.to_string());
        }
    }
    found
}

/// Whether the turn wrote the baton: a `handoff` call acknowledged.
pub fn handoff_declared(messages: &[Message]) -> bool {
    let results = results(messages);
    messages
        .iter()
        .flat_map(|message| &message.tool_calls)
        .any(|call| {
            is_tool(&call.name, HANDOFF_TOOL)
                && results
                    .get(call.id.as_str())
                    .is_some_and(|result| result.contains(HANDOFF_HEADER))
        })
}

/// The baton as a reader sees it: who wrote it and when, the note, the
/// session it belongs to once the record linked it, and whether a
/// session has picked it up.
pub struct Handoff {
    pub actor: String,
    pub written: Stamp,
    pub note: String,
    pub conversation: Option<String>,
    pub pending: bool,
}

/// The baton as the record and the tool write it: pending, or picked up
/// at a moment by a session.
pub fn handoff_document(
    actor: &str,
    written: Stamp,
    note: &str,
    conversation: Option<&str>,
    picked_up: Option<(Stamp, &str)>,
) -> String {
    let status = picked_up.map_or("pending".to_string(), |(at, by)| {
        format!("picked up {} by {by}", at.minutes())
    });
    let link = conversation.map_or(String::new(), |id| format!("- conversation: {id}\n"));
    format!(
        "# handoff from {actor}\n\n- from: {actor}\n- written: {}\n- status: {status}\n{link}\n{}\n",
        written.z(),
        strip(note)
    )
}

/// The baton read back, or None when the document is not one (a hand
/// edit): a reader shows nothing rather than guessing.
pub fn parse_handoff(content: &str) -> Option<Handoff> {
    let rest = content.split_once("\n\n").map_or("", |(_, rest)| rest);
    let (head, note) = rest.split_once("\n\n").unwrap_or((rest, ""));
    let fields = pairs(head);
    // `dict(pairs)`: the last value of a repeated key.
    let field = |name: &str| {
        fields
            .iter()
            .rev()
            .find(|(key, _)| *key == name)
            .map(|(_, value)| *value)
    };
    let written = Stamp::parse(&field("written")?.replace('Z', "+00:00")).ok()?;
    Some(Handoff {
        actor: field("from")?.to_string(),
        written,
        note: strip(note).to_string(),
        conversation: field("conversation").map(str::to_string),
        pending: field("status")? == "pending",
    })
}

/// How session start shows the baton: in full while pending and young,
/// one line while pending after that, not at all once picked up.
pub enum Tier {
    Full,
    Line,
}

pub fn handoff_tier(handoff: &Handoff, now: Stamp) -> Option<Tier> {
    if !handoff.pending {
        return None;
    }
    let young = now.instant() - handoff.written.instant() < HANDOFF_FULL_DAYS * DAY_MICROS;
    Some(if young { Tier::Full } else { Tier::Line })
}

/// Where the record verb's sessions document lands (§22): the mount at
/// `/project` when present, else the first read-write one; None when no
/// mount can take it.
pub fn sessions_mount(mounts: &[Mount]) -> Option<usize> {
    let writable = |mount: &&Mount| !mount.read_only && !mount.edit_only;
    let at_project = mounts
        .iter()
        .position(|mount| writable(&mount) && mount.path == PROJECT_MOUNT_PATH);
    at_project.or_else(|| mounts.iter().position(|mount| writable(&mount)))
}

/// The record linking the pending, unlinked baton to the session whose
/// span wrote it. Against the version read: a conflict means the pickup
/// landed first and knows the target already, so nothing is written.
/// True when it linked.
pub fn link_handoff(
    store: &FileStore,
    scope: &str,
    conversation: &str,
    actor: &Actor,
) -> Result<bool, Error> {
    let Some(document) = store.read(scope, HANDOFF_PATH)? else {
        return Ok(false);
    };
    if document.envelope.redacted {
        return Ok(false);
    }
    let Some(baton) = parse_handoff(&document.content) else {
        return Ok(false);
    };
    if !baton.pending || baton.conversation.is_some() {
        return Ok(false);
    }
    let content = handoff_document(
        &baton.actor,
        baton.written,
        &baton.note,
        Some(conversation),
        None,
    );
    match store.write(
        scope,
        HANDOFF_PATH,
        &content,
        actor,
        Some(document.envelope.version),
    ) {
        Ok(_) => Ok(true),
        Err(StoreError::Memory(error)) if error.code == "memory_conflict" => Ok(false),
        Err(error) => Err(error.into()),
    }
}

#[cfg(test)]
mod tests;
