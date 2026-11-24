//! A fold staged (DESIGN §40.54 item 21, T1b2c4): the conversation's log
//! read a line at a time, one turn held at once. A header becomes the whole
//! row its records assemble, refused by name before it is assembled when
//! its `bytes` pass `NEOSIAN_MAX_TURN_BYTES`; its records file is listed
//! for removal after the log is published, and when a client's seal made
//! it, the digests of its records are staged as its folded receipt. Every
//! whole row, a skeleton and a row a migration kept included, is copied
//! with its own bytes. Nothing outside the staging directory is written.

use std::fs::File;
use std::io::{self, BufRead, BufReader, BufWriter, Write};
use std::path::Path;

use super::super::{DRAFTS, drafts, not_utf8};
use super::{FILES, Folded, RECEIPTS, receipt};
use crate::conversation::Error;
use crate::conversation::rows::{TURNS, render_turn};
use crate::conversation::rows_header::{RecordHeader, Row, parse_row};
use crate::conversation::search::line_text;
use crate::limits::TURN_BYTES;
use crate::store::FileStore;
use crate::store::fileio::private;

fn created(path: &Path) -> io::Result<BufWriter<File>> {
    let file = private().create_new(true).write(true).open(path)?;
    Ok(BufWriter::new(file))
}

fn synced(mut out: BufWriter<File>) -> io::Result<()> {
    out.flush()?;
    out.get_ref().sync_all()
}

/// Whether a client's seal made this turn and may still be repeated: its
/// draft's `.sealed` header is there and names this very turn. A turn a
/// restore or a migration wrote has none, and a later draft bearing the
/// same id names another turn.
fn sealed(store: &FileStore, id: &str, header: &RecordHeader) -> Result<bool, Error> {
    let sealed = store.conversation_file(id, &format!("{DRAFTS}/{}.sealed", header.draft))?;
    if !sealed.try_exists()? {
        return Ok(false);
    }
    let text = std::fs::read_to_string(&sealed)?;
    Ok(matches!(
        parse_row(line_text(&text), 1, id, true)?,
        Row::Records(receipt) if receipt.turn == header.turn
    ))
}

/// Stage a sealed turn's folded receipt, a record at a time.
fn evidence(store: &FileStore, id: &str, header: &RecordHeader, to: &Path) -> Result<(), Error> {
    let path = store.record_file(id, header.turn)?;
    let records = drafts::open_records(&path, id, header.turn)?;
    let mut out = created(&to.join(header.turn.to_string()))?;
    receipt::write(&mut out, records, header, id)?;
    Ok(synced(out)?)
}

/// Stage `log` into `preparing`: the new log, the folded receipts, and
/// the turns whose records files the fold makes needless, one a line.
pub(super) fn stage(
    store: &FileStore,
    id: &str,
    log: &Path,
    preparing: &Path,
    max_turn_bytes: u64,
    found: &mut Folded,
) -> Result<(), Error> {
    let mut reader = BufReader::new(File::open(log)?);
    let mut turns = created(&preparing.join(TURNS))?;
    let mut files = created(&preparing.join(FILES))?;
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
            Row::Records(header) => header,
            Row::Whole(turn) => {
                if turn.redacted {
                    found.skeletons += 1;
                } else {
                    found.whole += 1;
                }
                turns.write_all(&raw)?;
                continue;
            }
        };
        if header.bytes > max_turn_bytes {
            return Err(Error::Value(format!(
                "resource limit {TURN_BYTES} exceeded: turn {} of {id} holds {} > \
                 {max_turn_bytes} bytes; raise the setting to fold it; nothing in this \
                 conversation was folded",
                header.turn, header.bytes
            )));
        }
        // A receipt of an earlier fold of this turn is the first one's.
        store.folded_file(id, header.turn)?;
        if sealed(store, id, &header)? && store.folded_owner(id, header.turn)?.is_none() {
            evidence(store, id, &header, &preparing.join(RECEIPTS))?;
        }
        writeln!(files, "{}", header.turn)?;
        let turn = store.whole(id, Row::Records(header), max_turn_bytes)?;
        turns.write_all(render_turn(&turn).as_bytes())?;
        found.folded += 1;
    }
    synced(files)?;
    synced(turns)?;
    Ok(())
}
