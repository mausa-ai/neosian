//! The sessions document a landed turn rewrites (DESIGN §20.9, §33,
//! §40.54 item 17.3): whole at every landing, with the lineage the turn
//! declared after what the document already says, the harness tool's
//! parent only on the document this landing creates, and a pending baton
//! linked to the session whose turn wrote it. The time it says the session
//! started is the first turn's, which the caller reads (ruling D7: from
//! that turn's header on a server that keeps records).

use std::io;

use crate::conversation::sessions::{
    continued_ids, continues, handoff_declared, link_handoff, parent_line, parents,
    sessions_document, sessions_path,
};
use crate::conversation::{Error, Turn};
use crate::state::State;
use crate::store::grammar::Actor;
use crate::store::time::Stamp;

/// Who wrote the turn and where its document lives.
pub struct Session<'a> {
    pub agent: &'a str,
    pub conversation_id: &'a str,
    pub actor: &'a str,
    pub scope: &'a str,
    pub parent: Option<&'a str>,
    pub provenance: Option<&'a str>,
}

/// Rewrites the document for `turn`, just appended, whose span's last
/// prompt is `last_prompt`, in a conversation whose first turn was
/// stored at `first`.
pub fn write(
    store: &dyn State,
    session: &Session,
    turn: &Turn,
    last_prompt: Option<&str>,
    first: Option<Stamp>,
) -> Result<(), Error> {
    let (id, scope) = (session.conversation_id, session.scope);
    let listed = store.read(scope, &sessions_path(id))?;
    let carried = listed
        .as_ref()
        .map_or_else(Vec::new, |listed| continues(&listed.content));
    // The harness tool's parent link (§40.50 item 6) stays as written.
    // The harness tool's parent from the environment names only a session
    // whose document this landing creates: never one that existed already.
    let mut parented: Vec<String> = Vec::new();
    let creates = listed.is_none();
    let named = session
        .parent
        .iter()
        .filter(|_| creates)
        .map(|p| p.to_string());
    for parent in listed
        .map_or_else(Vec::new, |listed| parents(&listed.content))
        .into_iter()
        .chain(named)
    {
        if !parented.contains(&parent) {
            parented.push(parent);
        }
    }
    let mut lineage: Vec<String> = Vec::new();
    for id in carried.into_iter().chain(continued_ids(&turn.messages)) {
        if !lineage.contains(&id) {
            lineage.push(id);
        }
    }
    let stamp =
        Actor::parse(&format!("{}#{}", session.actor, turn.turn)).map_err(io::Error::other)?;
    let started = first.unwrap_or(turn.created_at);
    let mut content =
        sessions_document(session.agent, id, started, last_prompt, turn.turn, &lineage);
    for parent in &parented {
        content.push_str(&parent_line(parent));
    }
    if let Some(provenance) = session.provenance {
        content.push_str(provenance);
    }
    store.write(scope, &sessions_path(id), &content, &stamp, None)?;
    if handoff_declared(&turn.messages) {
        link_handoff(store, scope, id, &stamp)?;
    }
    Ok(())
}
