//! Other restores of a conversation, and a restore removed (DESIGN §40.54
//! item 12 "Empty target", T1b2b1): one restore is open per conversation;
//! another touched within `NEOSIAN_DRAFT_SECONDS` is `restore_open`, an
//! older one is found without being changed and removed only by an opening
//! that has passed its checks, its bytes reported. A state that cannot be
//! read counts by its file's time, its bytes recounted from what is staged.

use std::fs;
use std::io::{self, BufRead, BufReader};
use std::path::{Path, PathBuf};

use super::drafts::parse_line;
use super::staging::Staging;
use super::stale::{modified, past, touched};
use crate::conversation::Error;
use crate::conversation::rows::{parse_projection, parse_redaction};
use crate::conversation::rows_header::{Row, parse_row};
use crate::conversation::turn_records::Sizes;
use crate::store::FileStore;

/// Restores found stale, each with the bytes it holds, and one still open.
pub type Others = (Vec<(PathBuf, u64)>, Option<String>);

/// The other restores of a conversation: those untouched for more than
/// `seconds` at `now`, with the bytes each holds, and the id of one still
/// open, if any. Nothing is changed. An unreadable state counts as
/// untouched since its file's time, and its bytes are recounted.
pub fn others(
    store: &FileStore,
    conversation_id: &str,
    except: &str,
    seconds: u64,
    now: i64,
) -> Result<Others, Error> {
    let parent = store.restores_dir(conversation_id)?;
    let entries = match fs::read_dir(&parent) {
        Ok(entries) => entries,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok((Vec::new(), None)),
        Err(error) => return Err(error.into()),
    };
    let (mut stale, mut open) = (Vec::new(), None);
    for entry in entries {
        let entry = entry?;
        let Some(name) = entry.file_name().to_str().map(str::to_string) else {
            continue;
        };
        if name == except || !entry.file_type()?.is_dir() {
            continue;
        }
        let staging = store.staging(conversation_id, &name)?;
        let (age, bytes) = match touched(&staging.state) {
            Some(found) => found,
            None => {
                let at = modified(&staging.state).or_else(|_| modified(&staging.dir))?;
                (at, recount(&staging, &Sizes::new(conversation_id, &name)))
            }
        };
        if past(now, age, seconds) {
            stale.push((staging.dir, bytes));
        } else if open.is_none() {
            open = Some(name);
        }
    }
    Ok((stale, open))
}

/// The size-rule bytes a restore whose state cannot be read holds: its
/// bookkeeping and every staged item read back, each file to its first
/// line that is not one.
pub fn recount(staging: &Staging, sizes: &Sizes) -> u64 {
    let each = |path: &Path, size: &dyn Fn(&str) -> Option<u64>| -> u64 {
        fs::File::open(path).map_or(0, |file| {
            let lines = BufReader::new(file).lines().map_while(Result::ok);
            lines.map_while(|text| size(&text)).sum()
        })
    };
    let headers = each(
        &staging.turns,
        &|text| match parse_row(text, 0, "", true).ok()? {
            Row::Records(header) => Some(sizes.header(header.actor.as_deref())),
            Row::Whole(turn) => Some(sizes.redacted_header(turn.actor.as_deref())),
        },
    );
    let mut records = 0;
    if let Ok(entries) = fs::read_dir(&staging.records) {
        for entry in entries.map_while(Result::ok) {
            let size = |text: &str| parse_line(text, 0, "").ok().map(|(r, _)| sizes.record(&r));
            records += each(&entry.path(), &size);
        }
    }
    let projections = each(&staging.projections, &|text| {
        parse_projection(text, 0, "")
            .ok()
            .map(|p| sizes.projection(&p.text))
    });
    let acts = each(&staging.redactions, &|text| {
        let act = parse_redaction(text, 0, "").ok()?;
        Some(sizes.act(act.actor.as_deref(), act.turns.len()))
    });
    sizes.bookkeeping() + headers + records + projections + acts
}

/// Remove a staged restore whole, then its conversation's directory when
/// empty; the parent made durable as the memory journal does.
pub fn remove(store: &FileStore, staging_dir: &Path) -> Result<(), Error> {
    match fs::remove_dir_all(staging_dir) {
        Err(error) if error.kind() != io::ErrorKind::NotFound => return Err(error.into()),
        _ => {}
    }
    if let Some(parent) = staging_dir.parent() {
        let _ = fs::remove_dir(parent);
        store.sync_existing(parent)?;
    }
    Ok(())
}
