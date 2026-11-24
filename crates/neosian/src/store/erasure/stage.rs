//! Stage replacements one row at a time. An error in a later row leaves
//! the visible turn log, projections, records and trail exactly as found.
use super::numbers::Numbers;
use super::publish::{FILES, Plan};
use super::selected;
use crate::conversation::rows::{
    PROJECTIONS, REDACTIONS, TURNS, parse_projection, parse_redaction, render_projection,
    render_redaction, render_turn,
};
use crate::conversation::rows_header::{Row, parse_row};
use crate::conversation::search::line_text;
use crate::conversation::{Error, Redaction, Turn};
use crate::store::time::Stamp;
use crate::store::{FileStore, fileio};
use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, BufWriter, Write};
use std::path::Path;

pub(super) fn each(
    path: &Path,
    mut visit: impl FnMut(&str, usize) -> Result<(), Error>,
) -> Result<(), Error> {
    let file = match File::open(path) {
        Ok(f) => f,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(e) => return Err(e.into()),
    };
    let mut reader = BufReader::new(file);
    let mut buf = String::new();
    let mut line = 0;
    loop {
        buf.clear();
        if reader.read_line(&mut buf)? == 0 {
            return Ok(());
        }
        line += 1;
        visit(&buf, line)?;
    }
}
fn copy_line(out: &mut impl Write, raw: &str) -> io::Result<()> {
    out.write_all(raw.as_bytes())?;
    if !raw.ends_with('\n') {
        out.write_all(b"\n")?;
    }
    Ok(())
}
fn output(dir: &Path, name: &str) -> io::Result<BufWriter<File>> {
    Ok(BufWriter::new(
        fileio::private()
            .create_new(true)
            .write(true)
            .open(dir.join(name))?,
    ))
}
fn sync(mut out: BufWriter<File>) -> io::Result<()> {
    out.flush()?;
    out.get_ref().sync_all()
}
pub(super) fn prepare(
    store: &FileStore,
    dir: &Path,
    id: &str,
    through: Option<u64>,
    named: Option<&[u64]>,
    actor: Option<&str>,
) -> Result<(Plan, usize), Error> {
    let path = |name| store.conversation_file(id, name);
    let all = through.is_none() && named.is_none();
    let records = store.records_enabled()?;
    let mut numbers = Numbers::new(&dir.join("numbers"))?;
    let mut turns = output(dir, TURNS)?;
    let mut files = output(dir, FILES)?;
    each(&path(TURNS)?, |raw, n| {
        let row = parse_row(line_text(raw), n, id, records)?;
        if !selected(row.turn(), through, named) {
            copy_line(&mut turns, raw)?;
            return Ok(());
        }
        numbers.push(row.turn())?;
        let skeleton = match row {
            Row::Whole(mut turn) => {
                turn.messages.clear();
                turn.redacted = true;
                turn
            }
            Row::Records(h) => {
                // Validate both destinations before recording a durable intent.
                store.record_file(id, h.turn)?;
                let sealed = store.conversation_file(id, &format!(".drafts/{}.sealed", h.draft))?;
                let remove_seal = if !all && sealed.try_exists()? {
                    let text = fs::read_to_string(&sealed)?;
                    let row = parse_row(line_text(&text), 1, id, true)?;
                    matches!(row, Row::Records(receipt) if receipt.turn == h.turn)
                } else {
                    false
                };
                serde_json::to_writer(&mut files, &(h.turn, remove_seal.then_some(&h.draft)))
                    .map_err(io::Error::other)?;
                files.write_all(b"\n")?;
                Turn {
                    conversation_id: id.into(),
                    turn: h.turn,
                    created_at: h.created_at,
                    actor: h.actor,
                    messages: Vec::new(),
                    redacted: true,
                }
            }
        };
        turns.write_all(render_turn(&skeleton).as_bytes())?;
        Ok(())
    })?;
    sync(files)?;
    sync(turns)?;
    let mut projections_changed = false;
    if numbers.count > 0 {
        let mut projections = output(dir, PROJECTIONS)?;
        each(&path(PROJECTIONS)?, |raw, n| {
            let mut p = parse_projection(line_text(raw), n, id)?;
            if !p.text.is_empty() && numbers.covers(p.turn - p.span + 1, p.turn)? {
                projections_changed = true;
                p.text.clear();
                projections.write_all(render_projection(&p).as_bytes())?;
            } else {
                copy_line(&mut projections, raw)?;
            }
            Ok(())
        })?;
        sync(projections)?;
        let mut trail = output(dir, REDACTIONS)?;
        each(&path(REDACTIONS)?, |raw, n| {
            parse_redaction(line_text(raw), n, id)?;
            copy_line(&mut trail, raw)?;
            Ok(())
        })?;
        let act = Redaction {
            conversation_id: id.into(),
            turns: Vec::new(),
            actor: actor.map(str::to_owned),
            created_at: Stamp::now(),
        };
        let line = render_redaction(&act);
        let prefix = line
            .strip_suffix("[]}\n")
            .expect("turns is the last trail field");
        trail.write_all(prefix.as_bytes())?;
        trail.write_all(b"[")?;
        let mut first = true;
        numbers.visit(|n| {
            if !first {
                trail.write_all(b",")?;
            }
            first = false;
            write!(trail, "{n}")
        })?;
        trail.write_all(b"]}\n")?;
        sync(trail)?;
    }
    let count = numbers.count;
    drop(numbers);
    fs::remove_file(dir.join("numbers"))?;
    Ok((
        Plan {
            all,
            turns: count > 0,
            projections: projections_changed,
        },
        count,
    ))
}
