//! One archived conversation as a restore stream (DESIGN §40.54 items 12
//! and 22): each turn's header then its records in `seq` order, then the
//! projections, then the trail acts. Each item comes with the position a
//! server answers once it holds that item, and the unit digest runs as the
//! stream passes. One source turn is held at a time (`read_turns_each`),
//! and the trail one page at a time. The projection list is read whole,
//! in the order the store keeps it, because that read is the order's
//! oracle.

use super::trail::Trail;
use crate::conversation::turn_records::{Header, Position, TurnRecord, UnitDigest, each_record};
use crate::conversation::{Error, Projection, Redaction, Turn};
use crate::state::State;

/// One item of the stream with its place where the stream gives it.
pub(super) enum Piece {
    Header(Header),
    Record(u64, TurnRecord),
    Projection(u64, Projection),
    Act(u64, Redaction),
}

/// What one pass over the stream counted, and its unit digest.
pub(super) struct Walked {
    pub turns: usize,
    pub projections: usize,
    pub redactions: usize,
    pub digest: String,
}

pub(super) type Visit<'v> = dyn FnMut(Piece, Position) -> Result<(), Error> + 'v;

/// A position's place along one stream. Positions only grow along a
/// stream, so two are ordered by these four numbers; `complete` follows
/// from them within one stream and is compared only for equality.
pub(super) fn place(at: Position) -> (u64, u64, u64, u64) {
    (at.turn, at.seq, at.projections, at.redactions)
}

/// Hands every item of `id`'s stream to `visit` with the position after
/// it. A source whose turns do not ascend cannot be restored and is
/// refused when the turn arrives.
pub(super) fn walk(
    source: &dyn State,
    trail: &Trail<'_>,
    id: &str,
    visit: &mut Visit<'_>,
) -> Result<Walked, Error> {
    let mut digest = UnitDigest::default();
    let mut at = Position::default();
    let mut turns = 0;
    source.read_turns_each(id, 0, None, &mut |turn| {
        if turns > 0 && turn.turn <= at.turn {
            return Err(Error::Value(format!(
                "conversation {id}: turn {} follows turn {} in the archive, and a restore takes \
                 turns ascending",
                turn.turn, at.turn
            )));
        }
        turns += 1;
        let header = header_of(id, &turn)?;
        let records = header.records;
        digest.header(&header);
        at = Position {
            turn: turn.turn,
            seq: 0,
            complete: records == 0,
            ..Position::default()
        };
        visit(Piece::Header(header), at)?;
        let mut failed = None;
        let converted = each_record(&turn.messages, &mut |record| {
            if failed.is_some() {
                return;
            }
            digest.record(turn.turn, &record);
            at.seq = record.seq + 1;
            at.complete = at.seq == records;
            if let Err(error) = visit(Piece::Record(turn.turn, record), at) {
                failed = Some(error);
            }
        });
        converted.map_err(|why| unfit(id, turn.turn, &why))?;
        failed.map_or(Ok(true), Err)
    })?;
    let projections = source.read_projections(id, 0, None)?;
    let counted = projections.len();
    for (index, entry) in projections.into_iter().enumerate() {
        digest.projection(&entry);
        at.projections = index as u64 + 1;
        visit(Piece::Projection(index as u64, entry), at)?;
    }
    trail.each_page(id, &mut |page| {
        for act in page {
            digest.act(&act);
            let index = at.redactions;
            at.redactions += 1;
            visit(Piece::Act(index, act), at)?;
        }
        Ok(())
    })?;
    Ok(Walked {
        turns,
        projections: counted,
        redactions: at.redactions as usize,
        digest: digest.finish(),
    })
}

/// A turn's header: its records counted and their texts' bytes summed by
/// one pass of the codec, which holds no record. A redacted turn is a
/// skeleton with no records.
fn header_of(id: &str, turn: &Turn) -> Result<Header, Error> {
    let (mut records, mut bytes) = (0_u64, 0_u64);
    if !turn.redacted {
        each_record(&turn.messages, &mut |record| {
            records += 1;
            bytes += record.text.len() as u64;
        })
        .map_err(|why| unfit(id, turn.turn, &why))?;
    }
    Ok(Header {
        turn: turn.turn,
        created_at: turn.created_at,
        actor: turn.actor.clone(),
        redacted: turn.redacted,
        records,
        bytes,
    })
}

fn unfit(id: &str, turn: u64, why: &str) -> Error {
    Error::Value(format!(
        "conversation {id}: turn {turn} cannot be restored in parts: {why}"
    ))
}
