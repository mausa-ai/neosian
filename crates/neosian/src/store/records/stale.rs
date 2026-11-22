//! The stale cleanup of a conversation's drafts (DESIGN §40.54 item 9
//! "Opening", after the review of 2481787): the drafts untouched for more
//! than `NEOSIAN_DRAFT_SECONDS`, found without changing any of them, so an
//! opening removes them only once its own checks have passed (finding 5).
//! An unreadable state counts as untouched since its file's time and is
//! cleaned like any other (finding 9).

use std::fs;
use std::io::{self, BufRead, BufReader};
use std::path::Path;

use super::drafts::{Paths, parse_line, read_state};
use crate::conversation::Error;
use crate::conversation::turn_records::Sizes;
use crate::store::fileio::within;
use crate::store::time::Stamp;

/// The draft states in `drafts`, by draft id.
fn states(drafts: &Path) -> Result<Vec<String>, Error> {
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
        if let Some(draft) = name.strip_suffix(".state") {
            found.push(draft.to_string());
        }
    }
    Ok(found)
}

/// When a file was last written, in UTC microseconds.
fn modified(path: &Path) -> Result<i64, Error> {
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
) -> Result<Vec<(Paths, u64)>, Error> {
    let mut found = Vec::new();
    for draft in states(drafts)? {
        let paths = Paths {
            records: drafts.join(format!("{draft}.jsonl")),
            state: drafts.join(format!("{draft}.state")),
            sealed: drafts.join(format!("{draft}.sealed")),
        };
        let readable = within(root, &paths.state)?;
        let state = if readable {
            read_state(&paths)?
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
        if now.saturating_sub(touched) > (seconds as i64).saturating_mul(1_000_000) {
            found.push((paths, bytes));
        }
    }
    Ok(found)
}

pub fn open_count(drafts: &Path) -> Result<usize, Error> {
    Ok(states(drafts)?.len())
}
