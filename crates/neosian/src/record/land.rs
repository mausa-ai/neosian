//! The Stop landing (`record/cli.py`'s `_land`, DESIGN §20.9, §33, §40.54
//! item 17, #447): every record goes to the open span first. At Stop (or
//! Cursor's answer) the span is closed into the conversation's pending
//! queue, so turn boundaries survive a failed landing, and the hook then
//! lands that queue in order, inline whatever the turn's size: each turn by
//! the foreign actor, as one draft in parts on a local store that keeps
//! records and as one `append_turn` elsewhere, with its sessions document.
//! A failed landing leaves the turns queued for the next Stop. An empty
//! span closes nothing. A span that cannot be read is held `unreadable`
//! with its bytes as they were, and that Stop fails with the reason, as
//! before the queue; later turns land. The span's bytes and records are
//! released once it is closed. A turn refused for good is held `refused`;
//! when it is the Stop's own turn the Stop fails with "the turn is held and
//! will not be sent again" and a hint naming where it stays (`held.rs`).

use std::fs;
use std::io;

use serde_json::Value;

use super::landing::draft::{draft_id, micros_z};
use super::landing::queue::Queue;
use super::landing::state::{Reason, State, describe};
use super::landing::turn::{Here, Recorded, land_queue};
use super::payload::{RESPONSE_EVENT, Record, STOP_EVENT, messages_of};
use super::settings::Settings;
use super::spool::{Spool, parse, text_of};
use crate::conversation::Error;
use crate::store::fileio::remove_file;
use crate::store::grammar::Actor;
use crate::store::time::Stamp;

/// What a landing left: the disposition, and the turn and document once
/// the closed turn is recorded.
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

/// One record into the spool, and at Stop (or Cursor's answer) the span
/// closed and the conversation's queue landed. The caller holds the
/// session lock.
pub fn land(
    settings: &Settings,
    spool: &Spool,
    id: &str,
    actor: &Actor,
    event: &str,
    record: &Record,
) -> Result<Landed, Error> {
    spool.append(id, record)?;
    let cursor = settings.agent == "cursor";
    if event != STOP_EVENT && !(cursor && event == RESPONSE_EVENT) {
        return Ok(only("spooled"));
    }
    let queue = Queue::new(&settings.spool);
    // The span's bytes and records are released once it is closed, before
    // its turn is read again to land (ruling E11).
    let n = {
        let span = spool.file(id);
        let bytes = match fs::read(&span) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == io::ErrorKind::NotFound => Vec::new(),
            Err(error) => return Err(error.into()),
        };
        let closing = |line: &str, length: u64| state(settings, id, actor, line, length);
        let records = match text_of(&bytes).and_then(|text| parse(text, &span)) {
            Ok(records) => records,
            Err(error) => {
                let reason = Reason::unreadable(error.to_string());
                let n = queue.close(&span, &bytes, id, Some(&reason), closing, || Ok(()))?;
                queue.persist(&queue.held_dir(id), n)?;
                closed(settings, id)?;
                return Err(error.into());
            }
        };
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
        if messages_of(&records).is_empty() {
            spool.clear(id)?;
            closed(settings, id)?;
            return Ok(only("empty"));
        }
        queue.close(&span, &bytes, id, None, closing, || Ok(()))?
    };
    closed(settings, id)?;
    // A turn that does not land now waits, so it is made durable first.
    let run = match land_queue(&queue, id, &here(settings)) {
        Ok(run) => run.unwrap_or_default(),
        Err(error) => {
            queue.persist(&queue.pending_dir(id), n)?;
            return Err(error);
        }
    };
    let held = run.refused.into_iter().find(|(m, _)| *m == n);
    let own = run.landed.into_iter().find(|(m, _)| *m == n);
    match (&held, &own) {
        (Some(_), _) => queue.persist(&queue.held_dir(id), n)?,
        (None, None) => queue.persist(&queue.pending_dir(id), n)?,
        (None, Some(_)) => {}
    }
    // This Stop's own turn held is the first thing to say (ruling E10),
    // before an older turn's sessions document that failed.
    if let Some((_, message)) = held {
        return Err(super::landing::held::error(
            &queue.held_dir(id),
            n,
            &message,
        ));
    }
    if let Some(error) = run.undocumented {
        return Err(error);
    }
    Ok(own.map_or_else(|| only("pending"), |(_, recorded)| recorded_as(recorded)))
}

fn recorded_as(recorded: Recorded) -> Landed {
    Landed {
        disposition: "recorded",
        conversation_id: Some(recorded.conversation_id),
        turn: Some(recorded.turn),
        document: Some(recorded.document),
        client: recorded.client,
    }
}

/// The span closed: a profile's pause-fence turn marker goes, then its
/// guard (its fingerprint is in the turn's state now), in the order
/// recording used before the queue, so a cut between them leaves a guard
/// that binds nothing rather than a stale marker.
fn closed(settings: &Settings, id: &str) -> io::Result<()> {
    if let Some(guard) = &settings.guard {
        super::pause::finish(&settings.spool, id)?;
        remove_file(guard)?;
    }
    Ok(())
}

/// The state of a turn this hook closes (ruling D3).
fn state(settings: &Settings, id: &str, actor: &Actor, line: &str, bytes: u64) -> State {
    let mount = &settings.mounts[settings.sessions_mount];
    let native = actor
        .as_str()
        .split_once(':')
        .map_or("", |(_, native)| native);
    let project = std::path::absolute(&settings.project).unwrap_or(settings.project.clone());
    State {
        draft: draft_id(id, line),
        next_seq: 0,
        fingerprint: settings.fingerprint.clone(),
        destination: Some(describe(&settings.store)),
        attempt: 0,
        closed_at: micros_z(Stamp::now()),
        bytes,
        agent: settings.agent.clone(),
        actor: actor.as_str().to_string(),
        session: native.to_string(),
        conversation_id: id.to_string(),
        scope: Some(mount.scope.clone()),
        mount: Some(mount.path.clone()),
        provenance: settings.provenance.clone(),
        parent: settings.parent.clone(),
        project: Some(project.to_string_lossy().into_owned()),
    }
}

/// What this hook can land: its destination, its policy, its mount.
fn here(settings: &Settings) -> Here<'_> {
    let mount = &settings.mounts[settings.sessions_mount];
    Here {
        selection: &settings.store,
        destination: describe(&settings.store),
        fingerprint: settings.fingerprint.as_deref(),
        scope: &mount.scope,
        mount: &mount.path,
        provenance: settings.provenance.as_deref(),
    }
}
