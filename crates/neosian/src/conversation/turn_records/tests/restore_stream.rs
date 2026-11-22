//! The rules of one restore stream (§40.54 item 12, T1b2b1) over item 12's
//! example: every item placed as new, repeated or past the next place, a
//! turn complete only when its records end whole at its header's bytes,
//! the state carried through JSON between parts as a store keeps it, ending
//! in the unit digest item 12 states.

use super::restores::{ID, example};
use crate::conversation::turn_records::{
    Header, Placed, Position, Restore, RestoreStream, StreamRefusal, TurnRecord, restore_id,
};

const MAX: u64 = 1 << 30;

/// The stream through JSON before each part, as a store keeps it.
fn through(
    stream: &RestoreStream,
    restore: &Restore,
) -> Result<(RestoreStream, Vec<Placed>), StreamRefusal> {
    let kept = serde_json::to_string(stream).unwrap();
    let mut next: RestoreStream = serde_json::from_str(&kept).unwrap();
    let placed = restore
        .items()
        .iter()
        .map(|item| next.admit(item, MAX))
        .collect::<Result<Vec<_>, _>>()?;
    Ok((next, placed))
}

#[test]
fn the_stream_places_every_item_and_runs_the_unit_digest() {
    let (headers, records, entry, act) = example();
    let part = |turns: &[Header], records: &[(u64, TurnRecord)]| Restore {
        restore: restore_id(ID),
        turns: turns.to_vec(),
        records: records.to_vec(),
        ..Restore::default()
    };
    let one = part(&headers[..1], &records[..2]);
    let (stream, placed) = through(&RestoreStream::default(), &one).unwrap();
    assert_eq!(placed, [Placed::New; 3]);
    let at = |turn, seq, complete| Position {
        turn,
        seq,
        complete,
        projections: 0,
        redactions: 0,
    };
    assert_eq!(stream.position(), at(1, 2, false));
    let (_, placed) = through(&stream, &one).unwrap();
    assert_eq!(
        placed,
        [Placed::Repeat; 3],
        "the same part again changes nothing"
    );
    let early = Restore {
        projections_from: Some(0),
        projections: vec![entry.clone()],
        ..part(&[], &[])
    };
    let gaps = [
        part(&headers[1..2], &[]),
        part(&[], &records[3..]),
        early,
        part(
            &[],
            &[(
                1,
                TurnRecord {
                    seq: 3,
                    ..records[2].1.clone()
                },
            )],
        ),
    ];
    for gap in &gaps {
        assert!(
            matches!(through(&stream, gap), Err(StreamRefusal::Gap)),
            "{gap:?}"
        );
    }
    let mut short = records[2].clone();
    short.1.text.pop();
    short.1.size -= 1;
    let refused = through(&stream, &part(&[], &[short])).unwrap_err();
    assert!(
        matches!(&refused, StreamRefusal::Invalid(why) if why.starts_with("turn 1:")),
        "{refused:?}"
    );
    let (stream, _) = through(&stream, &part(&[], &records[2..3])).unwrap();
    assert_eq!(stream.position(), at(1, 3, true));
    let late = part(
        &[],
        &[(
            1,
            TurnRecord {
                seq: 3,
                ..records[2].1.clone()
            },
        )],
    );
    assert_eq!(
        through(&stream, &late).unwrap_err(),
        StreamRefusal::Invalid("turn 1: record 3 is past its header's 3 records".into())
    );
    let big = Header {
        bytes: MAX + 1,
        records: 9,
        ..headers[2].clone()
    };
    assert_eq!(
        through(&stream, &part(&[big], &[])).unwrap_err(),
        StreamRefusal::Bound {
            bytes: MAX + 1,
            maximum: MAX
        }
    );
    let rest = Restore {
        projections_from: Some(0),
        projections: vec![entry.clone()],
        redactions_from: Some(0),
        redactions: vec![act.clone()],
        ..part(&headers[1..], &records[3..])
    };
    let (stream, placed) = through(&stream, &rest).unwrap();
    assert_eq!(placed, [Placed::New; 5]);
    assert_eq!(
        stream.position(),
        Position {
            turn: 3,
            seq: 1,
            complete: true,
            projections: 1,
            redactions: 1
        }
    );
    assert_eq!(
        stream.digest(),
        "6ef0ac035d795df5f9b79c7d7a720b8d40aa4cc5eb90c3a4a3b0d717ddb53b32"
    );
    let after = Restore {
        projections_from: Some(1),
        projections: vec![entry],
        ..part(&[], &[])
    };
    assert_eq!(
        through(&stream, &after).unwrap_err(),
        StreamRefusal::Invalid("projections come before trail acts".into())
    );
    let header = Header {
        turn: 4,
        ..headers[2].clone()
    };
    assert!(matches!(
        through(&stream, &part(&[header], &[])),
        Err(StreamRefusal::Invalid(why)) if why == "turn 4: turns come before projections and trail acts"
    ));
}
