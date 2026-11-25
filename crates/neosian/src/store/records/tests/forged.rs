//! A continuation is not a secret (§40.54 item 10, ruling R12): one made
//! up for a place outside the request's own selection is refused like one
//! of another request, so it reads nothing the request alone could not.
//! And a view of headers alone reads no record (R9).

use super::reading::{SESSION, mixed};
use crate::conversation::turn_records::{FOREIGN, ReadItem, ReadPosition, RecordRead};
use crate::store::FileStore;

/// What `read` answers from a continuation made up for (`turn`, `seq`):
/// the (turn, seq) of its records, or its refusal.
fn forged(
    store: &FileStore,
    read: &RecordRead,
    turn: u64,
    seq: u64,
) -> Result<Vec<(u64, u64)>, String> {
    let at = ReadPosition {
        turn,
        seq,
        ..ReadPosition::default()
    };
    let read = RecordRead {
        continuation: Some(at.encode(&read.fingerprint(SESSION))),
        ..read.clone()
    };
    let mut seen = Vec::new();
    let visited = store.read_records(SESSION, &read, &mut |item| {
        if let ReadItem::Record { turn, record, .. } = item {
            seen.push((turn, record.seq));
        }
        Ok(())
    });
    visited.map(|_| seen).map_err(|error| error.text())
}

#[test]
fn a_continuation_outside_the_requests_selection_is_refused() {
    // Turns 1 (whole, 16 records), 2 (redacted), 3 (16 records), 4 (whole).
    let (_home, store) = mixed("records-read-forged");
    let other = Err(FOREIGN.to_string());
    let one = RecordRead {
        turn: Some(3),
        ..RecordRead::default()
    };
    assert_eq!(forged(&store, &one, 1, 0), other);
    assert_eq!(forged(&store, &one, 2, 0), other);
    assert_eq!(forged(&store, &one, 4, 0), other);
    assert_eq!(forged(&store, &one, 3, 14).unwrap(), [(3, 14), (3, 15)]);
    let after = RecordRead {
        after: 2,
        through: Some(3),
        ..RecordRead::default()
    };
    assert_eq!(forged(&store, &after, 2, 0), other);
    assert_eq!(forged(&store, &after, 1, 15), other);
    assert_eq!(forged(&store, &after, 4, 0), other);
    assert_eq!(forged(&store, &after, 3, 15).unwrap(), [(3, 15)]);
    let from = RecordRead {
        turn: Some(3),
        seq: Some(5),
        ..RecordRead::default()
    };
    assert_eq!(forged(&store, &from, 3, 4), other);
    assert_eq!(forged(&store, &from, 3, 0), other);
    assert_eq!(forged(&store, &from, 3, 5).unwrap().len(), 11);
}

#[test]
fn a_continuation_of_a_piece_fetch_stays_in_its_piece() {
    // In turns 1 (converted) and 3 (records) the piece of records 2 to 14
    // lies between a one-record piece at 1 and another at 15.
    let (_home, store) = mixed("records-read-forged-piece");
    let other = Err(FOREIGN.to_string());
    for turn in [1, 3] {
        let piece = |seq| RecordRead {
            turn: Some(turn),
            seq: Some(seq),
            piece: true,
            ..RecordRead::default()
        };
        let rest = |from: u64| (from..15).map(|seq| (turn, seq)).collect::<Vec<_>>();
        for asked in [2, 9, 14] {
            let read = piece(asked);
            assert_eq!(forged(&store, &read, turn, 0), other, "{turn} {asked}");
            assert_eq!(forged(&store, &read, turn, 1), other, "{turn} {asked}");
            assert_eq!(forged(&store, &read, turn, 15), other, "{turn} {asked}");
            // Before and after the record asked for, inside its piece.
            for at in [3, 9, 14] {
                assert_eq!(forged(&store, &read, turn, at).unwrap(), rest(at));
            }
            assert_eq!(forged(&store, &read, turn + 1, 0), other);
        }
        let single = piece(1);
        assert_eq!(forged(&store, &single, turn, 0), other);
        assert_eq!(forged(&store, &single, turn, 2), other);
        assert_eq!(forged(&store, &single, turn, 1).unwrap(), [(turn, 1)]);
    }
}

#[test]
fn a_view_of_headers_alone_reads_no_record() {
    let (_home, store) = mixed("records-read-headers-only");
    std::fs::remove_file(store.record_file(SESSION, 3).unwrap()).unwrap();
    let headers = RecordRead {
        kinds: Some(Vec::new()),
        ..RecordRead::default()
    };
    let mut turns = Vec::new();
    store
        .read_records(SESSION, &headers, &mut |item| {
            let ReadItem::Header(header) = item else {
                panic!("a record in a view of headers");
            };
            turns.push((header.turn, header.records, header.bytes));
            Ok(())
        })
        .unwrap();
    assert_eq!(
        turns,
        [(1, 16, 812_400), (2, 0, 0), (3, 16, 812_400), (4, 2, 9)]
    );
    // Any other view opens the turn's records and refuses their absence.
    let whole = store.read_records(SESSION, &RecordRead::default(), &mut |_| Ok(()));
    assert!(whole.is_err());
}
