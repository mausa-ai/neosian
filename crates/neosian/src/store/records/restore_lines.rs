//! A restore part's new lines and its repeats (DESIGN §40.54 item 12,
//! T1b2b1): each new item's line as the conversation will hold it and its
//! size-rule bytes; the lines appended a file at a time with each file's
//! bytes counted in the state; repeated items compared with what is staged,
//! each staged file read once, a line at a time.

use super::staging::{self, RestoreState, Staging};
use crate::conversation::Error;
use crate::conversation::rows::{render_projection, render_redaction};
use crate::conversation::turn_records::{Item, Sizes};
use crate::store::FileStore;
use crate::store::fileio::append_line;

/// What a part appends, in stream order.
pub(super) enum Line {
    /// A header's line, and for a turn that keeps records a fresh file.
    Header(String, Option<u64>),
    Record(u64, String),
    Projection(String),
    Act(String),
}

impl FileStore {
    /// Append a part's new lines, a file at a time, counting each file's
    /// bytes in the state; a new turn's records file starts fresh, so one a
    /// cut-off request left is never continued.
    pub(super) fn write_lines(
        &self,
        conversation_id: &str,
        staging: &Staging,
        state: &mut RestoreState,
        lines: Vec<Line>,
    ) -> Result<(), Error> {
        let mut runs: Vec<(std::path::PathBuf, String)> = Vec::new();
        let mut push = |path: std::path::PathBuf, text: String| match runs.last_mut() {
            Some((last, held)) if *last == path => held.push_str(&text),
            _ => runs.push((path, text)),
        };
        for line in lines {
            match line {
                Line::Header(text, records) => {
                    state.files.turns += text.len() as u64;
                    push(staging.turns.clone(), text);
                    state.files.records = records.map(|_| 0);
                    if let Some(turn) = records {
                        let file = self.staged_records(staging, turn, conversation_id)?;
                        match std::fs::remove_file(&file) {
                            Err(error) if error.kind() != std::io::ErrorKind::NotFound => {
                                return Err(error.into());
                            }
                            _ => {}
                        }
                        push(file, String::new());
                    }
                }
                Line::Record(turn, text) => {
                    let counted = state.files.records.get_or_insert(0);
                    *counted += text.len() as u64;
                    push(self.staged_records(staging, turn, conversation_id)?, text);
                }
                Line::Projection(text) => {
                    state.files.projections += text.len() as u64;
                    push(staging.projections.clone(), text);
                }
                Line::Act(text) => {
                    state.files.redactions += text.len() as u64;
                    push(staging.redactions.clone(), text);
                }
            }
        }
        for (path, text) in runs {
            append_line(&path, &text, true)?;
        }
        Ok(())
    }

    /// Whether every repeated item equals what is staged at its place.
    pub(super) fn repeats_match(
        &self,
        conversation_id: &str,
        staging: &Staging,
        id: &str,
        repeats: &[Item<'_>],
    ) -> Result<bool, Error> {
        let mut at = 0;
        while at < repeats.len() {
            let same = match repeats[at] {
                Item::Header(_) => {
                    let run: Vec<_> = repeats[at..]
                        .iter()
                        .map_while(|item| match item {
                            Item::Header(header) => Some(*header),
                            _ => None,
                        })
                        .collect();
                    at += run.len();
                    let turns: Vec<u64> = run.iter().map(|header| header.turn).collect();
                    let stored = staging::headers_of(staging, &turns, conversation_id)?;
                    stored.iter().zip(&run).all(|(text, header)| {
                        let line = staging::header_line(header, id, conversation_id);
                        text.as_deref() == Some(line.trim_end())
                    })
                }
                Item::Record(turn, first) => {
                    let run: Vec<_> = repeats[at..]
                        .iter()
                        .map_while(|item| match item {
                            Item::Record(t, record) if *t == turn => Some(*record),
                            _ => None,
                        })
                        .collect();
                    at += run.len();
                    let file = self.staged_records(staging, turn, conversation_id)?;
                    let stored = staging::lines(&file, first.seq, run.len())?;
                    stored.len() == run.len()
                        && stored.iter().zip(&run).all(|(text, record)| {
                            super::drafts::parse_line(text, 0, conversation_id).is_ok_and(
                                |(_, digest)| digest == super::drafts::hex(&record.digest(None)),
                            )
                        })
                }
                Item::Projection(from, _) | Item::Act(from, _) => {
                    let projections = matches!(repeats[at], Item::Projection(..));
                    let run: Vec<String> = repeats[at..]
                        .iter()
                        .map_while(|item| match item {
                            Item::Projection(_, entry) if projections => {
                                Some(render_projection(entry))
                            }
                            Item::Act(_, act) if !projections => Some(render_redaction(act)),
                            _ => None,
                        })
                        .collect();
                    at += run.len();
                    let path = if projections {
                        &staging.projections
                    } else {
                        &staging.redactions
                    };
                    let stored = staging::lines(path, from, run.len())?;
                    stored.len() == run.len()
                        && stored
                            .iter()
                            .zip(&run)
                            .all(|(text, line)| text == line.trim_end())
                }
            };
            if !same {
                return Ok(false);
            }
        }
        Ok(true)
    }
}

/// A new item's line and its size-rule bytes (item 14).
pub(super) fn new_line(
    item: &Item<'_>,
    sizes: &Sizes,
    restore: &str,
    conversation_id: &str,
) -> (Line, u64) {
    match *item {
        Item::Header(header) => {
            let line = staging::header_line(header, restore, conversation_id);
            let actor = header.actor.as_deref();
            if header.redacted {
                (Line::Header(line, None), sizes.redacted_header(actor))
            } else {
                (Line::Header(line, Some(header.turn)), sizes.header(actor))
            }
        }
        Item::Record(turn, record) => (
            Line::Record(turn, super::drafts::line(record)),
            sizes.record(record),
        ),
        Item::Projection(_, entry) => (
            Line::Projection(render_projection(entry)),
            sizes.projection(&entry.text),
        ),
        Item::Act(_, act) => (
            Line::Act(render_redaction(act)),
            sizes.act(act.actor.as_deref(), act.turns.len()),
        ),
    }
}
