//! The parts of the archive import: a part's measured bytes are the
//! encoder's; a part fills to exactly 983,040 bytes and 2,048 items and not
//! one more, the commit's own bytes counted; a part never mixes repeats
//! with new items; and an item that cannot fit alone is refused by name.

use super::pack::{PART_BYTES, PART_ITEMS, Packer, Part, check};

/// The bounds item 8.2 sets for what the binary sends, written out here so
/// a changed constant fails these tests by behaviour, not only by value.
const BYTES: usize = 983_040;
const ITEMS: usize = 2_048;
use super::stream::Piece;
use super::tests::{ID, RESTORE, act, at, said, stamp};
use crate::conversation::Projection;
use crate::conversation::Redaction;
use crate::conversation::turn_records::{Header, Restore, TurnRecord, records_of};
use crate::remote::records::encode;

/// A record of `bytes` bytes of text.
fn line(seq: u64, bytes: usize) -> TurnRecord {
    let mut records = records_of(&[said("user", &"x".repeat(bytes))]).unwrap();
    let mut record = records.remove(0);
    record.seq = seq;
    record
}

fn small_act(index: usize) -> Redaction {
    let time = format!("2026-10-05T08:{:02}:{:02}Z", index / 60 % 60, index % 60);
    act(vec![1], &time)
}

/// Packs `pieces`, every one new, and answers its parts, each checked
/// against the encoder.
fn packed(pieces: Vec<Piece>) -> Result<Vec<Part>, String> {
    let mut packer = Packer::new(ID, RESTORE);
    let mut parts = Vec::new();
    for (index, piece) in pieces.into_iter().enumerate() {
        let end = at(1, index as u64, false, 0, 0);
        parts.extend(packer.push(piece, end, false).map_err(|e| e.text())?);
    }
    parts.extend(packer.finish().map_err(|e| e.text())?);
    for part in &parts {
        check(ID, part).map_err(|e| e.text())?;
    }
    Ok(parts)
}

/// A header and 17 records of turn 1, the last record `extra` bytes longer
/// than the length at which their one request, committing, is exactly
/// 983,040 bytes as the encoder writes it.
fn turn_one(extra: usize) -> Vec<Piece> {
    let build = |last: usize| {
        let mut records: Vec<TurnRecord> = (0..16).map(|seq| line(seq, 60_000)).collect();
        records.push(line(16, last));
        let head = Header::of(1, stamp("2026-10-02T09:00:00Z"), None, &records);
        (head, records)
    };
    let body = |last: usize| {
        let (head, records) = build(last);
        let restore = Restore {
            restore: RESTORE.into(),
            turns: vec![head],
            records: records.into_iter().map(|record| (1, record)).collect(),
            commit: true,
            ..Restore::default()
        };
        encode::restore(ID, &restore).len()
    };
    let mut last = 1;
    for _ in 0..8 {
        last = (last as i64 + BYTES as i64 - body(last) as i64) as usize;
    }
    assert_eq!(body(last), BYTES);
    let (head, records) = build(last + extra);
    let mut pieces = vec![Piece::Header(head)];
    pieces.extend(records.into_iter().map(|record| Piece::Record(1, record)));
    pieces
}

#[test]
fn a_part_fills_to_exactly_983040_bytes_and_not_one_more() {
    let full = packed(turn_one(0)).unwrap();
    assert_eq!(full.len(), 1);
    assert_eq!((full[0].bytes, full[0].restore.items_count()), (BYTES, 18));
    assert!(full[0].restore.commit);
    // One byte more: the last record goes, committing, to a part of its own.
    let over = packed(turn_one(1)).unwrap();
    assert_eq!(over.len(), 2);
    assert_eq!(over[0].restore.items_count(), 17);
    assert!(!over[0].restore.commit && over[1].restore.commit);
    assert_eq!(over[1].restore.records.len(), 1);
    // A part that does not commit fills to the bound as well: the commit's
    // bytes go to a later part, whose act then waits for the next one.
    let commit = r#","commit":true"#.len();
    let mut open = turn_one(0);
    if let Some(Piece::Record(_, record)) = open.last_mut() {
        record.text.push_str(&"x".repeat(commit));
        record.size += commit as u64;
    }
    if let Some(Piece::Header(head)) = open.first_mut() {
        head.bytes += commit as u64;
    }
    open.push(Piece::Act(0, small_act(0)));
    let parts = packed(open).unwrap();
    assert_eq!(parts.len(), 2);
    assert_eq!((parts[0].bytes, parts[0].restore.commit), (BYTES, false));
    assert_eq!(parts[1].restore.redactions.len(), 1);
    assert_eq!(PART_BYTES, BYTES);
}

#[test]
fn a_part_carries_2048_items_and_not_one_more() {
    let acts = |count: usize| {
        (0..count)
            .map(|index| Piece::Act(index as u64, small_act(index)))
            .collect::<Vec<_>>()
    };
    let full = packed(acts(ITEMS)).unwrap();
    assert_eq!(full.len(), 1);
    assert_eq!(full[0].restore.items_count(), ITEMS);
    let over = packed(acts(ITEMS + 1)).unwrap();
    let counts: Vec<usize> = over.iter().map(|p| p.restore.items_count()).collect();
    assert_eq!(counts, [ITEMS, 1]);
    assert_eq!(over[1].restore.redactions_from, Some(ITEMS as u64));
    assert_eq!(PART_ITEMS, ITEMS);
}

#[test]
fn every_kind_of_item_is_measured_as_the_encoder_writes_it() {
    let entry = |index: u64, text: &str| {
        let entry = Projection {
            turn: 3,
            kind: "log".into(),
            text: text.into(),
            span: 1,
        };
        Piece::Projection(index, entry)
    };
    let head = |turn: u64, actor: Option<&str>| {
        Piece::Header(Header {
            turn,
            created_at: stamp("2026-10-02T09:00:00.5+02:00"),
            actor: actor.map(str::to_string),
            redacted: actor.is_none(),
            records: 0,
            bytes: 0,
        })
    };
    let tricky = "quote \" slash \\ tab \t nul \u{0} snow \u{2603}".repeat(12_000);
    let pieces = vec![
        head(1, None),
        Piece::Record(2, line(0, 70)),
        head(3, Some("cli:é")),
        entry(9, &tricky),
        entry(10, &tricky),
        entry(11, "short"),
        Piece::Act(99_999, small_act(3)),
    ];
    // `packed` checks every part's measured bytes against the encoder.
    let parts = packed(pieces).unwrap();
    assert_eq!(parts.len(), 2);
    let last = &parts[1].restore;
    assert_eq!(
        (last.projections_from, last.redactions_from),
        (Some(10), Some(99_999))
    );
    assert!(last.commit);
}

#[test]
fn an_item_that_cannot_fit_a_part_alone_is_refused_by_name() {
    let entry = Projection {
        turn: 1,
        kind: "digest".into(),
        text: "d".repeat(BYTES),
        span: 1,
    };
    let refused = packed(vec![Piece::Projection(4, entry)]).err().unwrap();
    assert!(refused.starts_with("conversation cc-7f3a: projection 4 needs a restore part of "));
    assert!(refused.contains("past the 983040 bytes a part may carry"));
}

#[test]
fn repeats_and_new_items_never_share_a_part() {
    let mut packer = Packer::new(ID, RESTORE);
    let mut parts = Vec::new();
    for (index, replay) in [true, true, false, false].into_iter().enumerate() {
        let end = at(1, index as u64 + 1, false, 0, 0);
        let piece = Piece::Record(1, line(index as u64, 10));
        parts.extend(packer.push(piece, end, replay).unwrap());
    }
    parts.extend(packer.finish().unwrap());
    let shape: Vec<(bool, usize, bool)> = parts
        .iter()
        .map(|p| (p.replay, p.restore.items_count(), p.restore.commit))
        .collect();
    assert_eq!(shape, [(true, 2, false), (false, 2, true)]);
    assert_eq!(parts[0].end, at(1, 2, false, 0, 0));
}
