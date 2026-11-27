//! One conversation restored in parts (DESIGN §40.54 items 12 and 22, the
//! T1e2e slice of the T1e2 plan): the archive import's driver against a
//! target that keeps records. Its restore id is the one item 22 derives
//! from the conversation id, so a rerun of the same command finds it.
//!
//! The preflight probes the restore, which writes nothing, and passes over
//! the archive once with nothing sent: every part is packed and measured,
//! the unit digest computed, and the position a staged restore reports is
//! found in the stream. A committed restore is done only when its digest is
//! the archive's. An open one must stand exactly after one item of the
//! stream, sparse turn numbers, a turn's partial records and the projection
//! and act counts included, or the import refuses before anything is
//! written. The send then passes over the archive again: the items the
//! server holds go first, in parts of their own that repeat them (the
//! server compares each with what it stored, so an archive that changed is
//! found before the commit), then the rest, the last part committing. Each
//! answer must stand where its part leaves the stream, and the commit's
//! digest must be the archive's. A refusal or a lost answer stops the
//! import with the staged restore kept: a rerun probes before it writes
//! again, and a conversation whose restore has begun never goes whole.

use super::Unit;
use super::pack::{Packer, Part, check};
use super::stream::{Walked, place, walk};
use super::trail::Trail;
use crate::conversation::turn_records::{Position, Restore, Restored, Staged, restore_id};
use crate::conversation::{Error, Refused};
use crate::remote::records::Failure;
use crate::state::State;
use crate::state::records::{Fault, Records};

/// Where a records unit starts.
#[derive(Debug, PartialEq, Eq)]
pub(super) enum Start {
    /// Nothing staged: from the stream's start.
    Fresh,
    /// Staged up to this position: repeat what it holds, then go on.
    Resume(Position),
    /// Committed with the archive's own digest.
    Done,
}

/// A records unit after its preflight.
pub(super) struct Planned {
    pub id: String,
    pub start: Start,
    pub walked: Walked,
}

impl Planned {
    pub fn unit(&self) -> Unit {
        Unit {
            kind: "conversation",
            name: self.id.clone(),
            turns: self.walked.turns,
            projections: self.walked.projections,
            redactions: self.walked.redactions,
            ..Unit::default()
        }
    }
}

/// A target is occupied when it holds a turn or a projection.
pub(super) fn occupied(target: &dyn State, id: &str) -> Result<bool, Error> {
    Ok(target.last_turn_number(id)? != 0 || !target.read_projections(id, 0, Some(1))?.is_empty())
}

/// The preflight of one unit: a probe and a pass that sends nothing.
pub(super) fn prepare(
    records: &dyn Records,
    target: &dyn State,
    source: &dyn State,
    trail: &Trail<'_>,
    id: &str,
) -> Result<Planned, Error> {
    let restore = restore_id(id);
    let probe = Restore {
        restore: restore.clone(),
        ..Restore::default()
    };
    let probed = records
        .restore(id, probe)
        .map_err(|fault| failed(id, &restore, fault))?;
    if probed.restore != restore {
        return Err(answered(id, &probed, "a probe of", None));
    }
    let (start, from) = match (probed.state, probed.position) {
        (Staged::Committed, _) => (Start::Done, None),
        (Staged::Open, Some(at)) => (Start::Resume(at), Some(at)),
        (Staged::None, _) => (Start::Fresh, None),
        _ => return Err(answered(id, &probed, "a probe of", None)),
    };
    if start != Start::Done && occupied(target, id)? {
        return Err(Refused::conflict(id, "target_occupied").into());
    }
    let walked = pass(source, trail, id, &restore, from, &mut |part| {
        check(id, &part)
    })?;
    if start == Start::Done && probed.digest.as_deref() != Some(walked.digest.as_str()) {
        return Err(Error::Value(format!(
            "conversation {id}: restore {restore} is already committed on the server with unit \
             digest {}, but this archive's unit digest is {}, so the conversation there did not \
             come from this archive",
            probed.digest.as_deref().unwrap_or("null"),
            walked.digest
        )));
    }
    Ok(Planned {
        id: id.to_string(),
        start,
        walked,
    })
}

/// Sends the parts of one prepared unit and verifies its commit.
pub(super) fn send(
    records: &dyn Records,
    source: &dyn State,
    trail: &Trail<'_>,
    planned: &Planned,
) -> Result<(), Error> {
    let from = match planned.start {
        Start::Done => return Ok(()),
        Start::Fresh => None,
        Start::Resume(at) => Some(at),
    };
    let (id, restore) = (planned.id.as_str(), restore_id(&planned.id));
    let held = from.unwrap_or_default();
    let mut committed = None;
    let walked = pass(source, trail, id, &restore, from, &mut |part: Part| {
        check(id, &part)?;
        let due = if part.replay { held } else { part.end };
        let commit = part.restore.commit;
        let answer = records
            .restore(id, part.restore)
            .map_err(|fault| failed(id, &restore, fault))?;
        let state = if commit {
            Staged::Committed
        } else {
            Staged::Open
        };
        if answer.restore != restore || answer.state != state || answer.position != Some(due) {
            return Err(answered(id, &answer, "a part of", Some(due)));
        }
        committed = answer.digest.filter(|_| commit);
        Ok(())
    })?;
    if committed.as_deref() != Some(walked.digest.as_str()) {
        return Err(Error::Value(format!(
            "conversation {id}: restore {restore} was committed with unit digest {}, but this \
             archive's unit digest is {}",
            committed.as_deref().unwrap_or("null"),
            walked.digest
        )));
    }
    Ok(())
}

/// One pass over the stream, its parts handed to `sink` as they close.
/// `from` is where a staged restore stands: the items up to it are marked
/// as repeats, and a position that is not exactly after one item of this
/// stream is refused. The preflight's pass sends nothing, so it finds such
/// a position before any part leaves; the send's pass meets it only if the
/// archive changed in between, after parts that only repeat.
pub(super) fn pass(
    source: &dyn State,
    trail: &Trail<'_>,
    id: &str,
    restore: &str,
    from: Option<Position>,
    sink: &mut dyn FnMut(Part) -> Result<(), Error>,
) -> Result<Walked, Error> {
    let held = from.unwrap_or_default();
    let mut reached = held == Position::default();
    let mut packer = Packer::new(id, restore);
    let walked = walk(source, trail, id, &mut |piece, end| {
        let replay = !reached;
        if replay && place(end) >= place(held) && end != held {
            return Err(elsewhere(id, restore, held));
        }
        reached |= end == held;
        for part in packer.push(piece, end, replay)? {
            sink(part)?;
        }
        Ok(())
    })?;
    if !reached {
        return Err(elsewhere(id, restore, held));
    }
    for part in packer.finish()? {
        sink(part)?;
    }
    Ok(walked)
}

fn elsewhere(id: &str, restore: &str, at: Position) -> Error {
    Error::Value(format!(
        "conversation {id}: restore {restore} is staged on the server at {}, which is not a \
         place in this archive's stream, so it holds something else. Nothing new was written. \
         Remove it \
         with `neosian records drafts --discard` once it is older than NEOSIAN_DRAFT_SECONDS, \
         then import again",
        at.to_json()
    ))
}

fn answered(id: &str, answer: &Restored, what: &str, due: Option<Position>) -> Error {
    let at = answer.position.map(Position::to_json);
    let due = due.map_or(String::new(), |due| {
        format!(" where {} was due", due.to_json())
    });
    Error::Value(format!(
        "conversation {id}: the server answered {what} restore {} with restore {} in state {} at \
         {}{due}; the import stops",
        restore_id(id),
        answer.restore,
        answer.state.as_str(),
        at.map_or("null".to_string(), |at| at.to_string()),
    ))
}

/// A failed request as the import reports it: a refusal with its status,
/// code and `Retry-After`; a lost answer as a write whose outcome is not
/// known. Either way the staged restore stays for a rerun of the same
/// command, which probes before it writes again.
pub(super) fn failed(id: &str, restore: &str, fault: Fault) -> Error {
    let rerun = format!(
        "Restore {restore} stays staged and invisible; run the same import again to resume it \
         from the position the server holds"
    );
    match fault {
        Fault::Remote(Failure::Refused(refusal)) => {
            let code = refusal.code.as_deref().unwrap_or("no envelope");
            let wait = refusal.retry_after.map_or(String::new(), |seconds| {
                format!(", retry after {seconds} seconds")
            });
            Error::Value(format!(
                "conversation {id}: the server refused a restore request with {} ({code}): \
                 {}{wait}. {rerun}",
                refusal.status, refusal.message
            ))
        }
        Fault::Remote(failure) => Error::Value(format!(
            "conversation {id}: a restore request got no usable answer ({failure}), so the server \
             may have applied it. {rerun}"
        )),
        Fault::Local(error) => error,
    }
}
