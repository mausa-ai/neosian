//! The Stop landing (`record/cli.py`'s `_land`, DESIGN §20.9, §33, §40.54
//! items 17.1 and 17.3, #447): every record goes to the open span first.
//! At Stop (or Cursor's answer) the span is closed into the conversation's
//! pending queue, so turn boundaries survive a failed landing. The turn
//! lands inline only when its store is a local FileStore that keeps no
//! records (a profile's only when its history source is one and every
//! source is local), its file holds at most `INLINE_BYTES`, it is its
//! conversation's only queued turn and the conversation's lock is free
//! (rulings D6 and E12 of the T1e1 plan): as one `append_turn` with its
//! sessions document, by the rules of 17.4, and a failed landing keeps it
//! queued and fails the Stop as before. Every other closed turn is made
//! durable and left to a lander the caller starts (`landing/start.rs`):
//! the Stop answers `pending` and exits 0. An empty span closes nothing. A
//! span that cannot be read is held `unreadable` with its bytes as they
//! were, and that Stop fails with the reason, as before the queue. The
//! span's bytes and records are released once it is closed. A turn landed
//! inline and refused for good is held `refused`, and the Stop fails with
//! "the turn is held and will not be sent again" and a hint naming where
//! it stays (`held.rs`).

use std::fs;
use std::io;

use serde_json::Value;

use super::landing::cap::Cap;
use super::landing::draft::{draft_id, micros_z};
use super::landing::queue::Queue;
use super::landing::state::{Reason, State, describe};
use super::landing::turn::{Here, Recorded, Run, land_queue};
use super::payload::{RESPONSE_EVENT, Record, STOP_EVENT, messages_of};
use super::settings::Settings;
use super::spool::{Spool, parse, text_of};
use crate::conversation::Error;
use crate::profile::files::Endpoint;
use crate::remote::selection::Selection;
use crate::store::FileStore;
use crate::store::fileio::remove_file;
use crate::store::grammar::Actor;
use crate::store::time::Stamp;

/// The largest closed turn a Stop lands inline (DESIGN §40.54 items 17.1
/// and 23). The design's 1 MiB measured over Stop's 50 ms (a turn of 1 MB
/// took 52.8 ms, one of 128 KiB 48.5 ms on the bench's home), so the
/// threshold drops rather than the budget rising (owner question O3, taken
/// at its conservative reading).
pub const INLINE_BYTES: u64 = 128 << 10;

/// What a landing left: the disposition, the turn and document once the
/// closed turn is recorded, and whether a lander is to start for it.
pub struct Landed {
    pub disposition: &'static str,
    pub conversation_id: Option<String>,
    pub turn: Option<u64>,
    pub document: Option<String>,
    pub client: Option<String>,
    pub lander: bool,
}

fn only(disposition: &'static str) -> Landed {
    Landed {
        disposition,
        conversation_id: None,
        turn: None,
        document: None,
        client: None,
        lander: false,
    }
}

/// The closed turn waits, made durable, for a lander.
fn waits(queue: &Queue, id: &str, n: u64) -> Result<Landed, Error> {
    queue.persist(&queue.pending_dir(id), n)?;
    Ok(Landed {
        lander: true,
        ..only("pending")
    })
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
    // A store that cannot be read here is the lander's to meet.
    if !inline(settings, &queue, id, n).unwrap_or(false) {
        return waits(&queue, id, n);
    }
    // A turn that does not land now waits, so it is made durable first.
    let run = match land_queue(&queue, id, &here(settings, &Cap::NONE)) {
        Ok(Some(run)) => run,
        // The conversation's lock is taken: a lander lands the turn.
        Ok(None) => return waits(&queue, id, n),
        Err(error) => {
            queue.persist(&queue.pending_dir(id), n)?;
            return Err(error);
        }
    };
    settle(&queue, id, n, run)
}

/// What a run of the conversation's queue tells the Stop that closed turn
/// `n`: the turn recorded, or still pending, made durable; its hold, a
/// stop that keeps it, or an older turn's failed document, as a failure.
pub fn settle(queue: &Queue, id: &str, n: u64, run: Run) -> Result<Landed, Error> {
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
    // An answer that keeps the turn fails the Stop as a failed landing did.
    if let Some(stop) = run.stopped {
        return Err(Error::Value(stop.message().to_string()));
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
        lander: false,
    }
}

/// Whether turn `n`, just closed, lands inline (rulings D6 and E12): its
/// store is a local FileStore that keeps no records, a profile's only when
/// every source is local; it holds at most `INLINE_BYTES`; it is the
/// conversation's only queued turn. The landing itself finds the lock
/// free, or leaves the turn to a lander.
fn inline(settings: &Settings, queue: &Queue, id: &str, n: u64) -> Result<bool, Error> {
    let root = match &settings.store {
        Selection::File(root) => root,
        Selection::Profile(resolved) => {
            let local = |source: &str| match resolved.bindings.get(source).map(|b| &b.endpoint) {
                Some(Endpoint::File { root }) => Some(root),
                _ => None,
            };
            let every = resolved
                .bindings
                .keys()
                .all(|source| local(source).is_some());
            let history = resolved.profile.history.as_ref();
            match history.and_then(|history| local(&history.source)) {
                Some(root) if every => root,
                _ => return Ok(false),
            }
        }
        _ => return Ok(false),
    };
    // A root not made yet keeps no records, and is not made by this check.
    if root.is_dir() && FileStore::open(root)?.records_enabled()? {
        return Ok(false);
    }
    let file = Queue::part(&queue.pending_dir(id), n, "jsonl");
    Ok(fs::metadata(file)?.len() <= INLINE_BYTES && queue.pending(id)? == [n])
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
pub fn here<'a>(settings: &'a Settings, cap: &'a Cap) -> Here<'a> {
    let mount = &settings.mounts[settings.sessions_mount];
    Here {
        selection: &settings.store,
        destination: describe(&settings.store),
        fingerprint: settings.fingerprint.as_deref(),
        scope: &mount.scope,
        mount: &mount.path,
        provenance: settings.provenance.as_deref(),
        cap,
        patience: None,
        trial: None,
    }
}
