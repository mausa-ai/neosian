//! The stale cleanup of a conversation's drafts (DESIGN §40.54 item 9
//! "Opening", after the review of 2481787): the drafts untouched for more
//! than `NEOSIAN_DRAFT_SECONDS`, found without changing any of them, so an
//! opening removes them only once its own checks have passed (finding 5).
//! An unreadable state counts as untouched since its file's time and is
//! cleaned like any other (finding 9). A staged restore's age is read by
//! the same rule (`touched`, `past`, T1b2b1). A records file with neither a
//! state nor a seal's header is a first part whose writer stopped before
//! its state was published (finding 6 of the T1 review): it is stale by its
//! file's time and holds nothing to free, since no part of it was answered.

use std::fs;
use std::io::{self, BufRead, BufReader};
use std::path::Path;

use super::drafts::{Paths, parse_line, read_state};
use crate::conversation::Error;
use crate::conversation::turn_records::Sizes;
use crate::store::fileio::within;
use crate::store::time::Stamp;

/// The drafts in `drafts` by id, each with whether its state is there: a
/// state, or a records file left without a state or a seal's header.
fn found(drafts: &Path) -> Result<Vec<(String, bool)>, Error> {
    let entries = match fs::read_dir(drafts) {
        Ok(entries) => entries,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error.into()),
    };
    let mut found = Vec::new();
    for entry in entries {
        let path = entry?.path();
        let name = path
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or_default();
        let draft = |suffix| {
            name.strip_suffix(suffix)
                .filter(|id| crate::conversation::turn_records::check_draft_id(id).is_ok())
        };
        if let Some(draft) = draft(".state") {
            found.push((draft.to_string(), true));
        } else if let Some(draft) = draft(".jsonl") {
            let paths = Paths::entries(drafts, draft);
            if !paths.state.try_exists()? && !paths.sealed.try_exists()? {
                found.push((draft.to_string(), false));
            }
        }
    }
    Ok(found)
}

/// What a draft's or a restore's state says of its age: when it was last
/// touched (UTC microseconds) and the size-rule bytes it holds; both states
/// carry these fields.
#[derive(serde::Deserialize)]
struct Touched {
    neosian_format: u64,
    touched_at: String,
    bytes: u64,
}

/// A readable state's touch and bytes. Callers fall back to file times
/// when the state is absent, unreadable, or invalid.
pub(super) fn touched(path: &Path) -> Option<(i64, u64)> {
    let text = fs::read_to_string(path).ok()?;
    let state = serde_json::from_str::<Touched>(&text).ok()?;
    if state.neosian_format != 1 {
        return None;
    }
    Some((Stamp::parse(&state.touched_at).ok()?.instant(), state.bytes))
}

/// Whether something touched at `touched` is stale at `now`: untouched for
/// more than `seconds`, never at exactly that age.
pub(super) fn past(now: i64, touched: i64, seconds: u64) -> bool {
    now.saturating_sub(touched) > (seconds as i64).saturating_mul(1_000_000)
}

/// When a file was last written, in UTC microseconds.
pub(super) fn modified(path: &Path) -> Result<i64, Error> {
    let time = fs::symlink_metadata(path)?.modified()?;
    let since = time
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |since| since.as_micros());
    Ok(i64::try_from(since).unwrap_or(i64::MAX))
}

/// The size-rule bytes of a draft whose state is unreadable: its
/// bookkeeping and the records its file holds, read to the first line that
/// is not one.
fn recount(root: &Path, paths: &Paths, sizes: &Sizes) -> u64 {
    let records = within(root, &paths.records)
        .unwrap_or(false)
        .then(|| fs::File::open(&paths.records).ok())
        .flatten()
        .map(|file| {
            let lines = BufReader::new(file).lines().map_while(Result::ok);
            lines
                .map_while(|text| parse_line(&text, 0, "").ok())
                .map(|(record, _)| sizes.record(&record))
                .sum()
        });
    sizes.bookkeeping() + records.unwrap_or(0)
}

/// The drafts untouched for more than `seconds` at `now` (UTC
/// microseconds), each with the size-rule bytes it holds; nothing is
/// changed. An unreadable state counts as untouched since its file's time
/// (finding 9), so it never blocks another draft and is cleaned like one.
pub fn stale(
    root: &Path,
    drafts: &Path,
    seconds: u64,
    now: i64,
    conversation_id: &str,
) -> Result<Vec<(String, u64)>, Error> {
    let mut stale = Vec::new();
    for (draft, stated) in found(drafts)? {
        let paths = Paths::entries(drafts, &draft);
        if !stated {
            if past(now, modified(&paths.records)?, seconds) {
                stale.push((draft, 0));
            }
            continue;
        }
        let readable = within(root, &paths.state)?;
        let state = if readable {
            read_state(&paths)
        } else {
            Some(Err(()))
        };
        let (touched, bytes) = match state {
            None => continue,
            Some(Ok(state)) => {
                let touched = Stamp::parse(&state.touched_at).map_or(0, Stamp::instant);
                (touched, state.bytes)
            }
            Some(Err(())) => {
                let sizes = Sizes::new(conversation_id, &draft);
                (modified(&paths.state)?, recount(root, &paths, &sizes))
            }
        };
        if past(now, touched, seconds) {
            stale.push((draft, bytes));
        }
    }
    Ok(stale)
}

/// The drafts with a state that stay open once `old`, what `stale` found,
/// is removed.
pub fn open_after(drafts: &Path, old: &[(String, u64)]) -> Result<usize, Error> {
    let found = found(drafts)?.into_iter();
    let open =
        found.filter(|(draft, stated)| *stated && !old.iter().any(|(name, _)| name == draft));
    Ok(open.count())
}
