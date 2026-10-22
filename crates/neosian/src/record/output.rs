//! What the verb prints (`record/cli.py`, DESIGN §20.9, §21.7): nothing on
//! stdout unless the client's documented hook response or the `--json`
//! diagnostic envelope. Claude Code injects a hook's stdout as context on
//! every event but SessionStart through `hookSpecificOutput`, as Codex
//! does on every event; Cursor reads `additional_context` and answers every
//! other successful hook with an empty object; Codex's Stop wants a JSON
//! decision, which `{}` is none of; Muse kills a hook above 16 KiB.

use std::io::{self, Write};

use serde_json::{Value, json};

use super::payload::{SESSION_START_EVENT, STOP_EVENT};
use crate::pyfmt::dumps_spaced;
use crate::store::grammar::Actor;

/// Codex's `Stop` hook expects JSON on stdout at exit 0 ("plain text
/// output is invalid for this event", its hooks reference); Claude Code
/// takes silence.
const JSON_STOP_AGENTS: [&str; 1] = ["codex"];
/// Muse kills a hook above 16 KiB, including the final newline.
const MUSE_STDOUT_BYTES: usize = 16_382;

/// The diagnostic envelope: one object per call, every key present.
pub struct Envelope {
    pub event: String,
    pub session_id: String,
    pub actor: Actor,
    pub disposition: &'static str,
    pub conversation_id: Option<String>,
    pub turn: Option<u64>,
    pub document: Option<String>,
    pub client: Option<String>,
    pub context: Option<String>,
    /// The plugins' poll and delivery events answer with this key too.
    pub messages: Option<Vec<Value>>,
}

impl Envelope {
    pub fn json(&self) -> Value {
        let mut envelope = json!({
            "event": self.event,
            "session_id": self.session_id,
            "actor": self.actor.as_str(),
            "disposition": self.disposition,
            "conversation_id": self.conversation_id,
            "turn": self.turn,
            "document": self.document,
            "client": self.client,
            "context": self.context,
        });
        if let Some(messages) = &self.messages {
            envelope["messages"] = json!(messages);
        }
        envelope
    }
}

/// The last whole character at or below `limit` bytes:
/// `text.encode()[:limit].decode("utf-8", "ignore")`.
fn cut_bytes(text: &str, limit: usize) -> &str {
    let mut end = limit.min(text.len());
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    &text[..end]
}

/// A successful call's stdout, by the client's contract.
pub fn respond(
    envelope: &Envelope,
    agent: &str,
    json: bool,
    out: &mut impl Write,
) -> io::Result<()> {
    if json {
        return writeln!(out, "{}", envelope.json());
    }
    if let Some(context) = &envelope.context {
        if agent == "codex" || (agent == "claude-code" && envelope.event != SESSION_START_EVENT) {
            // Our context starts with '['; Codex treats that as JSON, so plain
            // output is rejected instead of injected into the session.
            let answer = json!({
                "hookSpecificOutput": {
                    "hookEventName": envelope.event,
                    "additionalContext": context,
                }
            });
            return writeln!(out, "{}", dumps_spaced(&answer, true, false));
        }
        if agent == "cursor" {
            let answer = json!({"additional_context": context});
            return writeln!(out, "{}", dumps_spaced(&answer, true, false));
        }
        let shown = if agent == "muse-code" {
            cut_bytes(context, MUSE_STDOUT_BYTES)
        } else {
            context
        };
        return writeln!(out, "{shown}");
    }
    if (envelope.event == STOP_EVENT && JSON_STOP_AGENTS.contains(&agent)) || agent == "cursor" {
        writeln!(out, "{{}}")?; // the client wants a JSON decision; this is none
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn envelope(event: &str, context: Option<&str>) -> Envelope {
        Envelope {
            event: event.to_string(),
            session_id: "s".to_string(),
            actor: Actor::parse("codex:s").unwrap(),
            disposition: "spooled",
            conversation_id: None,
            turn: None,
            document: None,
            context: context.map(str::to_string),
            messages: None,
            client: None,
        }
    }

    fn printed(envelope: &Envelope, agent: &str, json: bool) -> String {
        let mut out = Vec::new();
        respond(envelope, agent, json, &mut out).unwrap();
        String::from_utf8(out).unwrap()
    }

    #[test]
    fn each_client_reads_its_own_shape() {
        let start = envelope(SESSION_START_EVENT, Some("[index — é]"));
        assert_eq!(printed(&start, "claude-code", false), "[index — é]\n");
        assert_eq!(
            printed(&start, "codex", false),
            "{\"hookSpecificOutput\": {\"hookEventName\": \"SessionStart\", \"additionalContext\": \"[index \\u2014 \\u00e9]\"}}\n"
        );
        assert_eq!(
            printed(&start, "cursor", false),
            "{\"additional_context\": \"[index \\u2014 \\u00e9]\"}\n"
        );
        let prompt = envelope("UserPromptSubmit", Some("[inbox]"));
        assert!(
            printed(&prompt, "claude-code", false)
                .starts_with("{\"hookSpecificOutput\": {\"hookEventName\": \"UserPromptSubmit\"")
        );
        assert_eq!(printed(&prompt, "pi", false), "[inbox]\n");
        let stop = envelope(STOP_EVENT, None);
        assert_eq!(printed(&stop, "codex", false), "{}\n");
        assert_eq!(printed(&stop, "cursor", false), "{}\n");
        assert_eq!(printed(&stop, "claude-code", false), "");
        assert_eq!(
            printed(&envelope("UserPromptSubmit", None), "cursor", false),
            "{}\n"
        );
        assert_eq!(
            printed(&envelope("UserPromptSubmit", None), "codex", false),
            ""
        );
        let envelope_json: Value = serde_json::from_str(&printed(&stop, "codex", true)).unwrap();
        assert_eq!(envelope_json["actor"], "codex:s");
        assert_eq!(envelope_json["context"], Value::Null);
        assert_eq!(envelope_json.as_object().unwrap().len(), 9);
    }

    #[test]
    fn muse_is_cut_to_whole_characters_under_its_ceiling() {
        let long = format!("{}é", "x".repeat(MUSE_STDOUT_BYTES - 1));
        let shown = printed(
            &envelope(SESSION_START_EVENT, Some(&long)),
            "muse-code",
            false,
        );
        assert_eq!(shown, format!("{}\n", "x".repeat(MUSE_STDOUT_BYTES - 1)));
        assert_eq!(cut_bytes("abc", 10), "abc");
    }
}
