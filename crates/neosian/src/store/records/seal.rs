//! The seal (DESIGN §40.54 item 4.3): under the root lock, a durable
//! `sealing` marker holding the header, the draft's records renamed to
//! `records/<n>.jsonl`, the header appended to the turn log, the draft's
//! `.sealed` header written and its state removed, the marker removed.
//! Every step is safe to repeat, and every turn operation on the
//! conversation rolls a marked seal forward first, so no reader sees half
//! a seal and no writer takes its number. A constant cost: a marker, a
//! rename, a line and a small file, whatever the turn's size.

use std::fs;
use std::io;

use super::drafts::{DraftState, Paths};
use super::{DRAFTS, RECORD_FILES};
use crate::conversation::rows::TURNS;
use crate::conversation::rows_header::{RecordHeader, Row, parse_row, render_header};
use crate::conversation::search::line_text;
use crate::conversation::{Error, Refused};
use crate::store::FileStore;
use crate::store::fileio::{append_line, atomic_write, private_mkdir};
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
    ) -> Result<RecordHeader, Error> {
        let directory = self.conversation_dir(conversation_id)?;
        let turn = self.last_number(&directory.join(TURNS), conversation_id)? + 1;
        let header = RecordHeader {
            turn,
            created_at: Stamp::now(),
            actor: state.actor.clone(),
            records: state.check.next_seq(),
            bytes: state.check.bytes(),
            draft: draft.to_string(),
        };
        step(1)?;
        atomic_write(
            &directory.join(DRAFTS).join(SEALING),
            &render_header(&header),
            true,
        )?;
        step(2)?;
        self.roll_forward(conversation_id)
    }

    /// Roll a marked seal forward; nothing when none is marked.
    pub(in crate::store) fn settle(&self, conversation_id: &str) -> Result<(), Error> {
        let marker = self
            .conversation_dir(conversation_id)?
            .join(DRAFTS)
            .join(SEALING);
        if marker.is_file() {
            self.roll_forward(conversation_id)?;
        }
        Ok(())
    }

    fn roll_forward(&self, conversation_id: &str) -> Result<RecordHeader, Error> {
        let directory = self.conversation_dir(conversation_id)?;
        let drafts = directory.join(DRAFTS);
        let marker = drafts.join(SEALING);
        let text = fs::read_to_string(&marker)?;
        let Row::Records(header) = parse_row(line_text(&text), 1, conversation_id, true)? else {
            return Err(stuck(conversation_id, "its marker holds no header"));
        };
        let paths = Paths::new(&drafts, &header.draft);
        let target = directory
            .join(RECORD_FILES)
            .join(format!("{}.jsonl", header.turn));
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
            None if header.turn == 1 => append_line(&turns, &render_header(&header), true)?,
            Some(row) if row.turn() + 1 == header.turn => {
                append_line(&turns, &render_header(&header), true)?;
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
        step(5)?;
        fs::remove_file(&marker)?;
        Ok(header)
    }
}

fn stuck(conversation_id: &str, why: &str) -> Error {
    Refused::format_unsupported(
        conversation_id,
        &format!("a marked seal cannot be rolled forward: {why}"),
    )
    .into()
}
