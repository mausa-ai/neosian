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

use std::fs;
use std::io;

use super::drafts::{DraftState, Paths};
use super::{DRAFTS, RECORD_FILES};
use crate::conversation::rows::TURNS;
use crate::conversation::rows_header::{RecordHeader, Row, parse_row, render_header};
use crate::conversation::search::line_text;
use crate::conversation::{Error, Refused};
use crate::store::FileStore;
use crate::store::fileio::{append_turn_line, atomic_write, private_mkdir};
use crate::store::time::Stamp;

const SEALING: &str = "sealing";

#[cfg(test)]
thread_local! {
    /// The step after which a test's seal stops, as a killed process would.
    pub static CRASH_AFTER: std::cell::Cell<u8> = const { std::cell::Cell::new(0) };
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
        atomic_write(&marker, &render_header(&header), true)?;
        self.sync(&marker)?;
        step(2)?;
        self.roll_forward(conversation_id)
    }

    /// Roll a marked seal forward; nothing when none is marked, and a named
    /// refusal for a marker on a store without records.
    pub(in crate::store) fn settle(&self, conversation_id: &str) -> Result<(), Error> {
        self.settle_sweep(conversation_id)?;
        self.settle_erasure(conversation_id)?;
        self.settle_commit(conversation_id)?;
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
        let Row::Records(header) = parse_row(line_text(&text), 1, conversation_id, true)? else {
            return Err(stuck(conversation_id, "its marker holds no header"));
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

/// Make each directory's entries durable (Unix; elsewhere a rename is
/// made durable by the file system itself).
fn sync_dirs(directories: &[&std::path::Path]) -> Result<(), Error> {
    #[cfg(unix)]
    for directory in directories {
        if directory.is_dir() {
            fs::File::open(directory)?.sync_all()?;
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
