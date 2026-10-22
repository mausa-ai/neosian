//! The handoff pair on the server (`conversation/handoff.py`'s
//! `handoff_tools`, DESIGN §33): `continue_session` delivers a recorded
//! conversation whole and picks up its baton, the continued session's
//! inbox under it; `handoff` writes the baton, pending, for whoever
//! continues next. The server knows no session of its own.

use serde_json::{Map, Value};

use super::{Outcome, Tool, text};
use crate::assets::{CONTEXT, TOOLS, folded, mapping, quoted};
use crate::conversation::Error;
use crate::conversation::handoff::continue_conversation;
use crate::conversation::sessions::{HANDOFF_PATH, handoff_document, sessions_mount};
use crate::mcp::schema::{Field, Kind, Presence};
use crate::mcp::server::Server;
use crate::messaging::history::continuation_context;
use crate::messaging::mailbox::Mailbox;
use crate::store::error::Error as StoreError;
use crate::store::time::Stamp;
use crate::text::{len, strip};

const NOTE_CHARS: usize = 2048;

const CONTINUE_FIELDS: [Field; 2] = [
    Field {
        name: "conversation",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "session",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
];

const HANDOFF_FIELDS: [Field; 1] = [Field {
    name: "note",
    kind: Kind::Str,
    presence: Presence::Required,
}];

pub fn continue_session() -> Tool {
    Tool {
        name: "continue_session",
        description: quoted(TOOLS, "continue_session")
            .expect("tools.yaml carries continue_session"),
        params: mapping(TOOLS, "continue_session_params").expect("tools.yaml carries the params"),
        fields: &CONTINUE_FIELDS,
        read_only: false,
        destructive: false,
        call: continue_call,
    }
}

pub fn handoff() -> Tool {
    Tool {
        name: "handoff",
        description: quoted(TOOLS, "handoff").expect("tools.yaml carries handoff"),
        params: mapping(TOOLS, "handoff_params").expect("tools.yaml carries the params"),
        fields: &HANDOFF_FIELDS,
        read_only: false,
        destructive: true,
        call: handoff_call,
    }
}

fn scope(server: &Server) -> &str {
    let mounts = &server.memory.mounts;
    let at = sessions_mount(mounts).expect("the pair is served only with a sessions mount");
    &mounts[at].scope
}

fn continue_call(server: &Server, arguments: &Map<String, Value>) -> Outcome {
    let memory = &server.memory;
    let scope = scope(server);
    let conversation = text(arguments, "conversation");
    let outcome = continue_conversation(
        memory.store.as_ref(),
        scope,
        conversation,
        Some((&memory.actor, Stamp::now())),
    );
    let delivered = match outcome {
        Ok(Ok(delivered)) => delivered,
        Ok(Err(corrective)) => {
            let outcome = Outcome::fail(corrective.error);
            return match corrective.hint {
                Some(hint) => outcome.hint(hint),
                None => outcome,
            };
        }
        Err(Error::Conversation(refused)) => {
            return Outcome::fail(format!("[{}] {}", refused.code, refused.message));
        }
        Err(Error::Memory(error)) => {
            return Outcome::fail(format!("[{}] {}", error.code, error.message));
        }
        Err(error) => return Outcome::failed("continue_session", &error.text()),
    };
    let mut text = delivered.text;
    let reader = Mailbox::of_mounts(
        memory.store.as_ref(),
        &memory.mounts,
        text_session(arguments),
    )
    .with_actor(memory.actor.clone());
    match continuation_context(&reader, scope, &delivered.conversation, &memory.actor) {
        Ok(extra) if !extra.is_empty() => {
            text.push_str("\n\n");
            text.push_str(&extra);
        }
        Ok(_) => {}
        Err(error) => return Outcome::failed("continue_session", &sentence(&error)),
    }
    Outcome::ok(text)
}

fn text_session(arguments: &Map<String, Value>) -> Option<&str> {
    text(arguments, "session")
}

/// `str(exc)` of the exception that escaped the tool: the message alone.
fn sentence(error: &Error) -> String {
    match error {
        Error::Conversation(refused) => refused.message.clone(),
        Error::Memory(error) => error.message.clone(),
        Error::Value(sentence) => sentence.clone(),
        Error::Io(error) => error.to_string(),
    }
}

fn handoff_call(server: &Server, arguments: &Map<String, Value>) -> Outcome {
    let memory = &server.memory;
    let note = strip(text(arguments, "note").unwrap_or_default());
    let guide = folded(CONTEXT, "handoff_guide").expect("context.yaml carries the guide");
    if note.is_empty() {
        return Outcome::fail("Write the note first").hint(guide);
    }
    if len(note) > NOTE_CHARS {
        return Outcome::fail(format!(
            "The note is {} characters; keep it under {NOTE_CHARS}",
            len(note)
        ))
        .hint(guide);
    }
    let content = handoff_document(memory.actor.as_str(), Stamp::now(), note, None, None);
    match memory
        .store
        .write(scope(server), HANDOFF_PATH, &content, &memory.actor, None)
    {
        Ok(_) => {
            Outcome::ok(folded(CONTEXT, "handoff_recorded").expect("context.yaml carries the ack"))
        }
        Err(StoreError::Memory(error)) => {
            Outcome::fail(format!("[{}] {}", error.code, error.message))
        }
        Err(StoreError::Value(sentence)) => Outcome::fail(sentence),
        Err(StoreError::Io(error)) => Outcome::failed("handoff", &error.to_string()),
    }
}
