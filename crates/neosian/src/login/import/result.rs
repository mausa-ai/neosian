//! The import's result, read in the foreground (the L1c slice of the L1
//! plan, with L1e's N5): status is read for at most `POLL_SECONDS` of
//! waiting. An answer that names another session, or a success whose
//! counts differ from the commit, is not this import's result: the import
//! stops with its state kept. A failure naming one unit leaves that unit
//! out of the next session.

use std::io::Write;

use super::drive::{Outcome, Run, save};
use super::journal::{Journal, Left, Stage};
use super::session::{self, Miss, Progress};
use super::transfer::Why;

/// The longest the foreground waits for the host's result, in seconds.
pub(crate) const POLL_SECONDS: u64 = 60;

/// Reads status until a result, within `POLL_SECONDS` of waiting. None when
/// the host still has the session open, so the commit was not applied.
pub(super) fn poll(
    run: &mut Run<'_, impl Write>,
    journal: &mut Journal,
    id: &str,
) -> Option<Outcome> {
    let (mut waited, mut wait) = (0, 1);
    loop {
        let progress = match session::status(run.link, journal, id) {
            Err(e) => return Some(Outcome::Stopped(Why::Local(e))),
            Ok(Ok(progress)) => progress,
            Ok(Err(Miss::Refused(r))) if matches!(r.status, 404 | 410) => {
                return Some(Outcome::Ended);
            }
            Ok(Err(_)) => return Some(Outcome::Stopped(Why::Lost)),
        };
        if let Some(why) = progress.foreign(id, journal.committed().len()) {
            return Some(Outcome::Stopped(Why::Mismatch(why.into())));
        }
        match progress.status.as_str() {
            "succeeded" => return Some(Outcome::Succeeded(progress.targets)),
            "failed" => return Some(failed(run, journal, progress)),
            "waiting" => return Some(Outcome::Continuing(progress)),
            "open" => return None,
            _ => match pace(waited, wait) {
                None => return Some(Outcome::Continuing(progress)),
                Some(next) => {
                    (run.link.sleep)(std::time::Duration::from_secs(wait));
                    (waited, wait) = (waited + wait, next);
                }
            },
        }
    }
}

/// The wait after the one of `wait` seconds, which must still fit
/// `POLL_SECONDS` with the `waited` before it: 1, 2, 4, then 5 seconds.
pub(crate) fn pace(waited: u64, wait: u64) -> Option<u64> {
    (waited + wait <= POLL_SECONDS).then(|| (wait * 2).min(5))
}

/// A failed import. When it names one unit of this snapshot not yet left
/// out, the next session leaves that unit out (the host keeps the consent).
fn failed(run: &Run<'_, impl Write>, journal: &mut Journal, progress: Progress) -> Outcome {
    let named = progress.failed_unit.clone().filter(|(kind, name)| {
        let ours = journal
            .units
            .iter()
            .any(|u| &u.kind == kind && &u.name == name);
        let known = journal
            .refused
            .iter()
            .any(|l| &l.kind == kind && &l.name == name);
        ours && !known
    });
    let Some((kind, name)) = named else {
        return Outcome::Failed(progress, false);
    };
    journal.refused.push(Left {
        kind,
        name,
        reason: "unit_refused".into(),
    });
    journal.session = None;
    journal.omitted.clear();
    journal.stage = Stage::Begin;
    match save(run, journal) {
        Ok(()) => Outcome::Failed(progress, true),
        Err(_) => Outcome::Failed(progress, false),
    }
}
