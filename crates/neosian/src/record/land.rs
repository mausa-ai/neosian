//! The Stop landing (`record/cli.py`'s `_land`, DESIGN §20.9, §33): the
//! spool first, so a failed landing loses nothing; at Stop the span lands
//! as one turn by the foreign actor, the sessions document is rewritten
//! whole with the lineage the span declared, and a pending baton is linked
//! to the session whose span wrote it; the spool goes once the store is
//! done. An empty span lands nothing.

use std::io;

use serde_json::Value;

use super::payload::{RESPONSE_EVENT, Record, STOP_EVENT, last_prompt, messages_of};
use super::settings::Settings;
use super::spool::Spool;
use crate::conversation::Error;
use crate::conversation::sessions::{
    continued_ids, continues, handoff_declared, link_handoff, sessions_document, sessions_path,
};
use crate::store::grammar::Actor;

/// What a landing left: the disposition, and the turn and document once
/// the span is recorded.
pub struct Landed {
    pub disposition: &'static str,
    pub conversation_id: Option<String>,
    pub turn: Option<u64>,
    pub document: Option<String>,
    pub client: Option<String>,
}

fn only(disposition: &'static str) -> Landed {
    Landed {
        disposition,
        conversation_id: None,
        turn: None,
        document: None,
        client: None,
    }
}

fn kind(record: &Record) -> Option<&str> {
    record.get("kind").and_then(Value::as_str)
}

/// One record into the spool, and at Stop (or Cursor's answer) the whole
/// span into the store. The caller holds the session lock.
pub fn land(
    settings: &Settings,
    spool: &Spool,
    session_id: &str,
    actor: &Actor,
    event: &str,
    record: &Record,
) -> Result<Landed, Error> {
    spool.append(session_id, record)?;
    let cursor = settings.agent == "cursor";
    if event != STOP_EVENT && !(cursor && event == RESPONSE_EVENT) {
        return Ok(only("spooled"));
    }
    let records = spool.read(session_id)?;
    if cursor {
        let Some(last) = records.iter().rfind(|r| kind(r) == Some("stop")) else {
            return Ok(only("spooled"));
        };
        // The interactive CLI emits stop before afterAgentResponse. Keep the
        // turn whole regardless of which arrives first; failed turns lack text.
        if last.get("status").and_then(Value::as_str) == Some("completed")
            && !records.iter().any(|r| kind(r) == Some("assistant"))
            && !messages_of(&records).is_empty()
        {
            return Ok(only("spooled"));
        }
    }
    let messages = messages_of(&records);
    if messages.is_empty() {
        spool.clear(session_id)?;
        return Ok(only("empty"));
    }
    let mount = &settings.mounts[settings.sessions_mount];
    let scope = mount.scope.as_str();
    let store = settings.store.open()?;
    let turn = store.append_turn(session_id, messages, Some(actor.as_str().to_string()))?;
    // The lineage (§33): what this span declared, after what the document
    // already says; it is rewritten whole at every Stop.
    let listed = store.read(scope, &sessions_path(session_id))?;
    let carried = listed.map_or_else(Vec::new, |listed| continues(&listed.content));
    let mut lineage: Vec<String> = Vec::new();
    for id in carried.into_iter().chain(continued_ids(&turn.messages)) {
        if !lineage.contains(&id) {
            lineage.push(id);
        }
    }
    let stamp =
        Actor::parse(&format!("{}#{}", actor.as_str(), turn.turn)).map_err(io::Error::other)?;
    let started = store
        .first_turn_stamp(session_id)?
        .unwrap_or(turn.created_at);
    let content = sessions_document(
        &settings.agent,
        session_id,
        started,
        last_prompt(&records).as_deref(),
        turn.turn,
        &lineage,
    );
    store.write(scope, &sessions_path(session_id), &content, &stamp, None)?;
    if handoff_declared(&turn.messages) {
        link_handoff(store.as_ref(), scope, session_id, &stamp)?;
    }
    let client = store.client().map(str::to_string);
    drop(store);
    spool.clear(session_id)?;
    Ok(Landed {
        disposition: "recorded",
        client,
        conversation_id: Some(turn.conversation_id),
        turn: Some(turn.turn),
        document: Some(format!("/{}/{}", mount.path, sessions_path(session_id))),
    })
}
