//! The import session from Begin to its result (the L1c slice of the L1
//! plan, section F's ruling on resume and cleanup). The journal is written
//! before every step that changes the host, so a rerun starts where this
//! run stood:
//!
//! - **Begin.** `POST {import_endpoint}`: a new session, or this machine's
//!   open one with the units the host already holds, which are not sent
//!   again. A `403` means the approval did not include the import.
//! - **Transfer.** Each remaining unit; a conversation is probed first and
//!   resumes from where the host stands. A unit the host refuses by name is
//!   left out and the others go on. A conflict, a full session, any other
//!   refusal or a lost answer stops the import with its state kept.
//!   `410 session_expired` begins again, once.
//! - **Commit.** Every unit not left out, the host's omissions and the
//!   personal scope. A lost answer is uncertain until status is read:
//!   `open` means it was not applied and it is sent again, unchanged; any
//!   other status means it was. Units are never sent after a commit.
//! - **Result.** Status is read in the foreground for a bounded time.
//!   `succeeded` keeps a stage of its own until the directories are bound
//!   (L1d) and asks nothing more of the host. `failed` without a named unit,
//!   an ended session and a refused approval are terminal. Work the host goes
//!   on with, a stop and a failure naming one unit keep the state to resume.

use std::io::Write;
use std::path::Path;
use std::time::Instant;

use super::journal::{self, Journal, Left, Stage};
use super::link::Link;
use super::report;
use super::result::poll;
use super::session::{self, Miss, Progress, Target};
use super::snapshot::Snapshot;
use super::transfer::{self, Sent, Stop, Why};
use crate::login::output::{View, say};

/// Begin is asked at most this often in one run.
const BEGINS: u32 = 2;

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Outcome {
    Succeeded(Vec<Target>),
    /// The host goes on alone: queued or running past the bound, or waiting.
    Continuing(Progress),
    /// The approval did not include the import, or the consent ended.
    NotAuthorized,
    /// Failed on the host; true when it named a unit the next session leaves out.
    Failed(Progress, bool),
    /// Every unit was left out; the session was discarded.
    Nothing,
    /// The host's session is gone or expired.
    Ended,
    Stopped(Why),
}

impl Outcome {
    /// A proved end: nothing a rerun could do with the kept state.
    pub(crate) fn terminal(&self) -> bool {
        !matches!(
            self,
            Self::Continuing(_) | Self::Stopped(_) | Self::Failed(_, true)
        )
    }
}

pub(crate) struct Run<'a, W: Write> {
    pub link: &'a Link,
    pub home: &'a Path,
    /// The discovery URL the person signed in to.
    pub origin: &'a str,
    pub ui: &'a mut View<W>,
    pub verbose: bool,
}

pub(super) fn save(run: &Run<'_, impl Write>, journal: &Journal) -> Result<(), Stop> {
    journal::write(run.home, journal).map_err(|e| Stop::Kept(Why::Local(e)))
}

pub(super) fn stopped(miss: Miss) -> Outcome {
    match miss {
        Miss::Refused(r) => Outcome::Stopped(Why::Refused(r.status, r.code)),
        Miss::Lost => Outcome::Stopped(Why::Lost),
    }
}

/// Runs the session from the journal's stage to an outcome.
pub(crate) fn drive(
    run: &mut Run<'_, impl Write>,
    journal: &mut Journal,
    snapshot: Option<&Snapshot>,
) -> Outcome {
    let mut begins = 0;
    // A commit found in the journal may have been applied: status first.
    let mut confirm = journal.stage == Stage::Commit;
    let mut commits = 0;
    loop {
        let id = journal
            .session
            .as_ref()
            .map(|s| s.id.clone())
            .unwrap_or_default();
        let step = match journal.stage {
            Stage::Begin | Stage::Transfer => {
                begins += 1;
                if begins > BEGINS {
                    return Outcome::Stopped(Why::Refused(410, Some("session_expired".into())));
                }
                match transfer_all(run, journal, snapshot) {
                    Ok(Some(outcome)) => return outcome,
                    Ok(None) => {
                        confirm = false;
                        continue;
                    }
                    Err(Stop::Expired) => {
                        journal.stage = Stage::Begin;
                        save(run, journal)
                    }
                    Err(stop) => Err(stop),
                }
            }
            Stage::Commit if confirm => match session::status(run.link, journal, &id) {
                Err(e) => return Outcome::Stopped(Why::Local(e)),
                Ok(Ok(p)) if p.id.as_deref().is_some_and(|named| named != id) => {
                    return Outcome::Stopped(Why::Mismatch("another_session".into()));
                }
                Ok(Ok(p)) if p.status == "open" => {
                    confirm = false;
                    continue;
                }
                Ok(Ok(_)) => {
                    journal.stage = Stage::Committed;
                    save(run, journal)
                }
                Ok(Err(Miss::Refused(r))) if matches!(r.status, 404 | 410) => {
                    return Outcome::Ended;
                }
                Ok(Err(_)) => return Outcome::Stopped(Why::Lost),
            },
            Stage::Commit => {
                commits += 1;
                if commits > 2 {
                    return Outcome::Stopped(Why::Lost);
                }
                match session::commit(run.link, journal, &id) {
                    Err(e) => return Outcome::Stopped(Why::Local(e)),
                    Ok(Ok(())) => {
                        journal.stage = Stage::Committed;
                        save(run, journal)
                    }
                    Ok(Err(Miss::Lost)) => {
                        confirm = true;
                        continue;
                    }
                    Ok(Err(Miss::Refused(r))) if r.status == 403 => return Outcome::NotAuthorized,
                    Ok(Err(Miss::Refused(r))) if r.status == 410 => {
                        journal.stage = Stage::Begin;
                        save(run, journal)
                    }
                    Ok(Err(Miss::Refused(r))) if r.is(409, "units_mismatch") => {
                        return Outcome::Stopped(Why::Conflict("units_mismatch".into()));
                    }
                    Ok(Err(miss)) => return stopped(miss),
                }
            }
            Stage::Succeeded => return Outcome::Succeeded(journal.targets.clone()),
            Stage::Committed => match poll(run, journal, &id) {
                Some(outcome) => return outcome,
                None => {
                    journal.stage = Stage::Commit;
                    confirm = false;
                    save(run, journal)
                }
            },
        };
        if let Err(Stop::Kept(why)) = step {
            return Outcome::Stopped(why);
        }
    }
}

/// Begin, then every unit still to send. Some(outcome) when the run ends
/// here; None when the commit is next.
fn transfer_all(
    run: &mut Run<'_, impl Write>,
    journal: &mut Journal,
    snapshot: Option<&Snapshot>,
) -> Result<Option<Outcome>, Stop> {
    let begun = match session::begin(run.link, run.origin, &journal.import_endpoint) {
        Err(e) => return Err(Stop::Kept(Why::Local(e))),
        Ok(Ok(begun)) => begun,
        Ok(Err(Miss::Refused(r))) if matches!(r.status, 403 | 410) => {
            return Ok(Some(Outcome::NotAuthorized));
        }
        Ok(Err(miss)) => return Ok(Some(stopped(miss))),
    };
    if journal.session.as_ref().map(|s| &s.id) != Some(&begun.opened.id) {
        journal.omitted.clear();
    }
    journal.session = Some(begun.opened.clone());
    journal.stage = Stage::Transfer;
    save(run, journal)?;
    let lost = |e: std::io::Error| Stop::Kept(Why::Local(e.to_string()));
    report::limits(run.ui, begun.limits, journal).map_err(lost)?;
    let snapshot = snapshot.ok_or_else(|| Stop::Kept(Why::Local(say("import_copy_gone"))))?;
    let store = transfer::store(snapshot)?;
    let todo: Vec<_> = transfer::pending(journal, &begun.received)?
        .into_iter()
        .cloned()
        .collect();
    let mut last = Instant::now();
    for (n, unit) in todo.iter().enumerate() {
        let url = &begun.opened.transfer_url;
        let sent = match unit.kind.as_str() {
            "scope" => transfer::scope(run.link, url, &store, unit)?,
            _ => transfer::conversation(run.link, url, &store, unit)?,
        };
        if let Sent::Omitted(reason) = sent {
            journal.omitted.push(Left {
                kind: unit.kind.clone(),
                name: unit.name.clone(),
                reason: reason.into(),
            });
            save(run, journal)?;
        }
        let shown = (run.verbose, &mut last);
        report::progress(run.ui, (n + 1, todo.len()), unit, &sent, shown).map_err(lost)?;
    }
    if journal.committed().is_empty() {
        // Ended only once the host confirms that nothing of it is left (L1e, N4).
        return Ok(Some(
            match session::discard(run.link, journal, &begun.opened.id) {
                Err(e) => Outcome::Stopped(Why::Local(e)),
                Ok(Ok(())) => Outcome::Nothing,
                Ok(Err(Miss::Refused(r))) if matches!(r.status, 404 | 410) => Outcome::Nothing,
                Ok(Err(miss)) => stopped(miss),
            },
        ));
    }
    journal.stage = Stage::Commit;
    save(run, journal)?;
    Ok(None)
}
