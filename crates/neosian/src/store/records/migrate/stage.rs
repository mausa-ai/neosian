//! A migration staged (DESIGN §40.54 item 21, T1b2c3): the conversation's
//! log read a line at a time, one whole turn held at once. A whole turn
//! becomes a header and a records file only when the line this release
//! writes for it is its stored line, its records rebuild that same line,
//! it fits `NEOSIAN_MAX_RECORD_TURN_BYTES` and its number has no records
//! file yet. Then a later fold writes the stored bytes again. Every other
//! row (a header, a skeleton, a whole turn that fails one of those) is
//! copied with its own bytes, a missing final newline included, and counted.

use std::fs::File;
use std::io::{self, BufRead, BufReader, BufWriter, Write};
use std::path::Path;

use serde_json::Value;

use super::super::{RECORD_FILES, drafts, not_utf8};
use crate::conversation::rows::{TURNS, render_turn};
use crate::conversation::rows_header::{RecordHeader, Row, parse_row, render_header};
use crate::conversation::search::line_text;
use crate::conversation::turn_records::migration::{Kept, Migrated, carried, migration_id};
use crate::conversation::turn_records::{messages_of, records_of};
use crate::conversation::{Error, Turn};
use crate::store::FileStore;
use crate::store::fileio::private;

/// Why a whole turn stays whole.
enum Why {
    Spelling,
    Content,
    Size,
    Number,
}

impl Why {
    fn count(self, kept: &mut Kept) {
        match self {
            Self::Spelling => kept.spelling += 1,
            Self::Content => kept.content += 1,
            Self::Size => kept.size += 1,
            Self::Number => kept.number += 1,
        }
    }
}

fn created(path: &Path) -> io::Result<BufWriter<File>> {
    let file = private().create_new(true).write(true).open(path)?;
    Ok(BufWriter::new(file))
}

fn synced(mut out: BufWriter<File>) -> io::Result<()> {
    out.flush()?;
    out.get_ref().sync_all()
}

/// Stage `log` into `preparing`: the new log, and a records file for each
/// turn migrated. Nothing outside `preparing` is written.
pub(super) fn stage(
    store: &FileStore,
    id: &str,
    log: &Path,
    preparing: &Path,
    max_record_turn_bytes: u64,
    found: &mut Migrated,
) -> Result<(), Error> {
    let owner = migration_id(id);
    let mut reader = BufReader::new(File::open(log)?);
    let mut turns = created(&preparing.join(TURNS))?;
    let (mut raw, mut number) = (Vec::new(), 0_usize);
    loop {
        raw.clear();
        if reader.read_until(b'\n', &mut raw)? == 0 {
            break;
        }
        number += 1;
        let Ok(stored) = std::str::from_utf8(&raw) else {
            return Err(not_utf8(id, number));
        };
        let header = match parse_row(line_text(stored), number, id, true)? {
            Row::Records(_) => {
                found.records += 1;
                None
            }
            Row::Whole(turn) if turn.redacted => {
                found.skeletons += 1;
                None
            }
            Row::Whole(turn) => {
                let place = (preparing, owner.as_str(), max_record_turn_bytes);
                match convert(store, id, turn, stored, place)? {
                    Ok(header) => {
                        found.migrated += 1;
                        Some(header)
                    }
                    Err(why) => {
                        why.count(&mut found.kept);
                        None
                    }
                }
            }
        };
        match header {
            Some(header) => turns.write_all(render_header(&header).as_bytes())?,
            None => turns.write_all(&raw)?,
        }
    }
    synced(turns)?;
    Ok(())
}

/// Whether the stored line is its turn in another spelling, nothing more.
fn same_value(stored: &str, written: &str) -> bool {
    let value = |text: &str| serde_json::from_str::<Value>(line_text(text)).ok();
    matches!((value(stored), value(written)), (Some(a), Some(b)) if carried(&a, &b))
}

/// One whole turn's header, its records file staged; or why it stays whole.
fn convert(
    store: &FileStore,
    id: &str,
    turn: Turn,
    stored: &str,
    (preparing, owner, max_record_turn_bytes): (&Path, &str, u64),
) -> Result<Result<RecordHeader, Why>, Error> {
    let written = render_turn(&turn);
    if written != stored {
        let why = if same_value(stored, &written) {
            Why::Spelling
        } else {
            Why::Content
        };
        return Ok(Err(why));
    }
    drop(written);
    let Ok(records) = records_of(&turn.messages) else {
        return Ok(Err(Why::Content));
    };
    let (number, created_at, actor) = (turn.turn, turn.created_at, turn.actor);
    drop(turn.messages);
    let rebuilt = messages_of(&records).map(|messages| Turn {
        conversation_id: id.to_string(),
        turn: number,
        messages,
        created_at,
        actor: actor.clone(),
        redacted: false,
    });
    // The codec promises this; it is checked before the stored line is given up.
    let same = rebuilt.is_ok_and(|turn| render_turn(&turn) == stored);
    if !same {
        return Ok(Err(Why::Content));
    }
    let bytes: u64 = records.iter().map(|record| record.text.len() as u64).sum();
    if bytes > max_record_turn_bytes {
        return Ok(Err(Why::Size));
    }
    if store.record_file(id, number)?.try_exists()? {
        return Ok(Err(Why::Number));
    }
    let path = preparing.join(RECORD_FILES).join(format!("{number}.jsonl"));
    let mut out = match created(&path) {
        Ok(out) => out,
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
            return Ok(Err(Why::Number));
        }
        Err(error) => return Err(error.into()),
    };
    for record in &records {
        out.write_all(drafts::line(record).as_bytes())?;
    }
    synced(out)?;
    Ok(Ok(RecordHeader {
        turn: number,
        created_at,
        actor,
        records: records.len() as u64,
        bytes,
        draft: owner.to_string(),
    }))
}
