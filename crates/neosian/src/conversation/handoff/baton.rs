//! The baton (DESIGN §33): which pending note a continue call delivers,
//! which session a bare call means, and the note marked picked up against
//! the version that was read (R1 slice D).

use super::{Answer, Corrective};
use crate::conversation::Error;
use crate::conversation::sessions::{
    HANDOFF_PATH, Handoff, SESSIONS_PREFIX, handoff_document, parse_handoff,
};
use crate::state::State;
use crate::store::error::Error as StoreError;
use crate::store::grammar::Actor;
use crate::store::time::Stamp;
use crate::store::{Document, Entry};

/// The baton marked picked up by a new version against the version that
/// was read; on a conflict (the departing Stop linking it in the same
/// beat) once more against the fresh one, still pending or not.
pub(super) fn picked_up(
    store: &dyn State,
    scope: &str,
    document: &Document,
    baton: &Handoff,
    target: &str,
    by: &Actor,
    now: Stamp,
) -> Result<(), Error> {
    let mut expected = document.envelope.version;
    let mut baton = Handoff {
        actor: baton.actor.clone(),
        written: baton.written,
        note: baton.note.clone(),
        conversation: baton.conversation.clone(),
        pending: baton.pending,
    };
    for attempt in 0..2 {
        let content = handoff_document(
            &baton.actor,
            baton.written,
            &baton.note,
            Some(target),
            Some((now, by.as_str())),
        );
        match store.write(scope, HANDOFF_PATH, &content, by, Some(expected)) {
            Ok(_) => return Ok(()),
            Err(StoreError::Memory(error)) if error.code == "memory_conflict" && attempt == 0 => {
                let Some(fresh) = store.read(scope, HANDOFF_PATH)? else {
                    return Ok(());
                };
                let Some(again) = pending(Some(&fresh)) else {
                    return Ok(());
                };
                expected = fresh.envelope.version;
                baton = again;
            }
            Err(error) => return Err(error.into()),
        }
    }
    Ok(())
}

pub(super) fn pending(document: Option<&Document>) -> Option<Handoff> {
    let document = document.filter(|document| !document.envelope.redacted)?;
    parse_handoff(&document.content).filter(|baton| baton.pending)
}

/// Whether the pending note is the delivered session's: linked to it, or
/// unlinked when the call named nothing (the default chose it).
pub(super) fn belongs(baton: &Handoff, target: &str, named: bool) -> bool {
    baton.conversation.as_deref() == Some(target) || (baton.conversation.is_none() && !named)
}

/// The session a bare call means: the note's when the record linked it,
/// else the newest listed session, written after the note when one is
/// pending and unlinked.
pub(super) fn default_target(
    store: &dyn State,
    scope: &str,
    baton: Option<&Handoff>,
) -> Answer<String> {
    if let Some(linked) = baton.and_then(|baton| baton.conversation.clone()) {
        return Ok(Ok(linked));
    }
    let mut entries: Vec<Entry> = store
        .list_documents(scope, SESSIONS_PREFIX)?
        .into_iter()
        .filter(|entry| !entry.redacted)
        .collect();
    entries.sort_by_key(|entry| std::cmp::Reverse(entry.updated_at.instant()));
    if let Some(baton) = baton {
        entries.retain(|entry| entry.updated_at.instant() > baton.written.instant());
        if entries.is_empty() {
            return Ok(Err(Corrective {
                error: "The handoff note's session has not landed yet".to_string(),
                hint: Some("Name the conversation, or retry in a moment."),
            }));
        }
    }
    Ok(match entries.first() {
        Some(entry) => Ok(entry.path[SESSIONS_PREFIX.len()..].to_string()),
        None => Err(Corrective {
            error: "Nothing to continue: no session is listed in this scope".to_string(),
            hint: Some("Name a conversation id to continue one anyway."),
        }),
    })
}
