//! The seal (DESIGN §40.54 item 4.3): under the root lock, a durable
//! `sealing` marker holding the header, the draft's records renamed to
//! `records/<n>.jsonl`, the header appended to the turn log, the draft's
//! `.sealed` header written and its state removed, the marker removed.
//! Every step is safe to repeat, and every turn operation on the
//! conversation rolls a marked seal forward first, so no reader sees half
//! a seal and no writer takes its number. As the memory journal does, the
//! directories are synced after the marker is written, before it is
//! removed and after (the review of 2481787, finding 8), and temp files a
//! killed write left are swept. A marker on a store without records is
//! refused by name, never rolled forward (finding 12). A constant cost: a
//! marker, a rename, a line, a small file and a few directory syncs,
//! whatever the turn's size.
//!
//! The marker's second line is the turn log's length before the append
//! (finding 2 of the T1 review). Recovery first cuts the log back to it
//! when what lies past it is a partial write of the header line the seal
//! appends, so a torn append is repaired and appended again; anything else
//! past it is left as it is and refused. A marker of one line, written
//! before this, rolls forward as it did.

use std::fs::{self, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom};
use std::path::Path;

use super::drafts::{DraftState, Paths};
use super::{DRAFTS, RECORD_FILES};
use crate::conversation::rows::TURNS;
use crate::conversation::rows_header::{RecordHeader, Row, parse_row, render_header};
use crate::conversation::search::line_text;
use crate::conversation::{Error, Refused};
use crate::store::FileStore;
use crate::store::fileio::{append_turn_line, atomic_write, needs_separator, private_mkdir};
use crate::store::time::Stamp;

const SEALING: &str = "sealing";

/// The marker's second line: the turn log's length before the append.
#[derive(serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct Before {
    log_bytes: u64,
}

#[cfg(test)]
thread_local! {
    /// The step after which a test's seal stops, as a killed process would.
    pub static CRASH_AFTER: std::cell::Cell<u8> = const { std::cell::Cell::new(0) };
    /// The directories `sync_dirs` synced on this thread, in order.
    pub static SYNCED: std::cell::RefCell<Vec<std::path::PathBuf>> =
        const { std::cell::RefCell::new(Vec::new()) };
}

fn step(done: u8) -> Result<(), Error> {
    #[cfg(test)]
    if CRASH_AFTER.with(std::cell::Cell::get) == done {
        return Err(Error::Value(format!("stopped after seal step {done}")));
    }
    let _ = done;
    Ok(())
}

impl FileStore {
    /// Seal an open draft whose records are all in its file: the turn's
    /// header, once visible.
    #[cfg_attr(not(test), allow(dead_code))]
    pub(super) fn seal(
        &self,
        conversation_id: &str,
        draft: &str,
        state: &DraftState,
        turn: u64,
    ) -> Result<RecordHeader, Error> {
        let header = RecordHeader {
            turn,
            created_at: Stamp::now(),
            actor: state.actor.clone(),
            records: state.check.next_seq(),
            bytes: state.check.bytes(),
            draft: draft.to_string(),
        };
        step(1)?;
        let marker = self.drafts_dir(conversation_id)?.join(SEALING);
        let turns = self.conversation_dir(conversation_id)?.join(TURNS);
        let log_bytes = match fs::metadata(&turns) {
            Ok(metadata) => metadata.len(),
            Err(error) if error.kind() == io::ErrorKind::NotFound => 0,
            Err(error) => return Err(error.into()),
        };
        let before = serde_json::to_string(&Before { log_bytes }).map_err(io::Error::other)?;
        atomic_write(
            &marker,
            &format!("{}{before}\n", render_header(&header)),
            true,
        )?;
        self.sync(&marker)?;
        step(2)?;
        self.roll_forward(conversation_id)
    }

    /// Roll a marked seal forward; nothing when none is marked, and a named
    /// refusal for a marker on a store without records.
    pub(in crate::store) fn settle(&self, conversation_id: &str) -> Result<(), Error> {
        self.settle_sweep(conversation_id)?;
        self.settle_stored(conversation_id)
    }

    /// `settle` without the sweep: the journals kept in the conversation's
    /// own directory, for a caller that knows which directories are its.
    pub(super) fn settle_stored(&self, conversation_id: &str) -> Result<(), Error> {
        self.settle_erasure(conversation_id)?;
        self.settle_commit(conversation_id)?;
        self.settle_migration(conversation_id)?;
        self.settle_fold(conversation_id)?;
        let marker = self
            .conversation_dir(conversation_id)?
            .join(DRAFTS)
            .join(SEALING);
        if !marker.is_file() {
            return Ok(());
        }
        self.drafts_dir(conversation_id)?;
        if !self.records_enabled()? {
            return Err(Refused::format_unsupported(
                conversation_id,
                "a seal marker is left on a store without records: run `neosian records enable` \
                 to finish its seal, or remove the conversation's .drafts directory to drop it",
            )
            .into());
        }
        self.roll_forward(conversation_id)?;
        Ok(())
    }

    fn roll_forward(&self, conversation_id: &str) -> Result<RecordHeader, Error> {
        let directory = self.conversation_dir(conversation_id)?;
        let drafts = self.drafts_dir(conversation_id)?;
        self.sweep(&drafts)?;
        let marker = drafts.join(SEALING);
        let text = fs::read_to_string(&marker)?;
        let (first, second) = text.split_at(text.find('\n').map_or(text.len(), |at| at + 1));
        let Row::Records(header) = parse_row(line_text(first), 1, conversation_id, true)? else {
            return Err(stuck(conversation_id, "its marker holds no header"));
        };
        let before = match second {
            "" => None,
            line => Some(
                serde_json::from_str::<Before>(line_text(line))
                    .map_err(|_| stuck(conversation_id, "its marker's log length is unreadable"))?
                    .log_bytes,
            ),
        };
        let paths = Paths::new(&self.root, &drafts, &header.draft, conversation_id)?;
        let target = self.record_file(conversation_id, header.turn)?;
        if paths.records.is_file() {
            if target.exists() {
                return Err(stuck(conversation_id, "its records file is already taken"));
            }
            private_mkdir(&directory.join(RECORD_FILES))?;
            fs::rename(&paths.records, &target)?;
        }
        step(3)?;
        let turns = directory.join(TURNS);
        if let Some(before) = before {
            untear(&turns, before, &render_header(&header))?;
        }
        match self.last_row(&turns, conversation_id)? {
            None if header.turn == 1 => append_turn_line(&turns, &render_header(&header), true)?,
            Some(row) if row.turn().checked_add(1) == Some(header.turn) => {
                append_turn_line(&turns, &render_header(&header), true)?;
            }
            Some(Row::Records(last)) if last == header => {}
            _ => return Err(stuck(conversation_id, "the turn log moved past its number")),
        }
        step(4)?;
        atomic_write(&paths.sealed, &render_header(&header), true)?;
        match fs::remove_file(&paths.state) {
            Err(error) if error.kind() != io::ErrorKind::NotFound => return Err(error.into()),
            _ => {}
        }
        sync_dirs(&[&directory.join(RECORD_FILES), &drafts, &directory])?;
        step(5)?;
        fs::remove_file(&marker)?;
        sync_dirs(&[&drafts])?;
        Ok(header)
    }
}

/// Cuts the turn log back to `before` bytes when what lies past them is a
/// partial write of `line` as `append_turn_line` appends it (a line ending
/// first when the log's last byte is not one), synced. Nothing past
/// `before`, the whole line, or anything else is left as it is: the row
/// checks after this decide.
fn untear(turns: &Path, before: u64, line: &str) -> Result<(), Error> {
    let mut log = match OpenOptions::new().read(true).write(true).open(turns) {
        Ok(log) => log,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error.into()),
    };
    let length = log.metadata()?.len();
    if length <= before {
        return Ok(());
    }
    let mut intended = Vec::with_capacity(line.len() + 1);
    if needs_separator(&mut log, before)? {
        intended.push(b'\n');
    }
    intended.extend_from_slice(line.as_bytes());
    let Some(written) = usize::try_from(length - before)
        .ok()
        .filter(|written| *written < intended.len())
    else {
        return Ok(());
    };
    let mut suffix = vec![0; written];
    log.seek(SeekFrom::Start(before))?;
    log.read_exact(&mut suffix)?;
    if intended.starts_with(&suffix) {
        log.set_len(before)?;
        log.sync_all()?;
    }
    Ok(())
}

/// Make each directory's entries durable (Unix; elsewhere a rename is
/// made durable by the file system itself).
pub(super) fn sync_dirs(directories: &[&std::path::Path]) -> Result<(), Error> {
    #[cfg(unix)]
    for directory in directories {
        if directory.is_dir() {
            fs::File::open(directory)?.sync_all()?;
            #[cfg(test)]
            SYNCED.with(|synced| synced.borrow_mut().push(directory.to_path_buf()));
        }
    }
    let _ = directories;
    Ok(())
}

fn stuck(conversation_id: &str, why: &str) -> Error {
    Refused::format_unsupported(
        conversation_id,
        &format!("a marked seal cannot be rolled forward: {why}"),
    )
    .into()
}
