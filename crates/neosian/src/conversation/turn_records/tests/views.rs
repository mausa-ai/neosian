//! Views of one turn (§40.54 item 10): heads and tails worked out by hand,
//! a head collected chunk by chunk equal to the head and tail of the whole
//! piece however it was split, kinds selected, and a piece fetch stopping
//! at its piece's end.

use serde_json::json;

use super::generate::{self, Rng};
use super::message;
use crate::conversation::turn_records::{
    Kind, ReadItem, RecordRead, TEXT_BYTES, TurnRecord, Viewer, encoded_len, head, records_of, tail,
};

#[test]
fn heads_and_tails_count_encoded_bytes_quotes_included() {
    assert_eq!(head("abcdef", 5), "abc");
    assert_eq!(tail("abcdef", 5), "def");
    assert_eq!(head("a\"bc", 5), "a\"");
    assert_eq!(tail("ab\u{1}", 7), "");
    assert_eq!(tail("ab\u{1}", 8), "\u{1}");
    assert_eq!(tail("ab\u{1}", 9), "b\u{1}");
    assert_eq!(head("é中😀x", 7), "é中");
    assert_eq!(head("abc", 1), "");
    assert_eq!(head("abc", 2), "");
    assert_eq!((head("abc", 99), tail("abc", 99)), ("abc", "abc"));
    assert_eq!(head("", 2), "");
}

fn items(read: &RecordRead, records: &[TurnRecord]) -> Vec<(ReadItem, u64)> {
    let mut viewer = Viewer::new(read, 7, read.seq.unwrap_or(0));
    records
        .iter()
        .flat_map(|record| viewer.push(record))
        .collect()
}

/// One piece's records cut at random places within the bound.
fn cut(text: &str, rng: &mut Rng) -> Vec<TurnRecord> {
    let template = records_of(&[message(
        &json!({"role": "tool", "content": "x", "tool_call_id": "t"}),
    )])
    .unwrap()[0]
        .clone();
    let mut records = Vec::new();
    let mut rest = text;
    loop {
        let fits = encoded_len(rest) <= TEXT_BYTES;
        let end = if rest.is_empty() || (fits && rng.one_in(2)) {
            rest.len()
        } else {
            let mut end = 1 + rng.below(rest.len().min(20_000) as u64) as usize;
            while !rest.is_char_boundary(end) {
                end += 1;
            }
            end
        };
        let chunk = records.len() as u64;
        records.push(TurnRecord {
            seq: chunk,
            chunk,
            size: text.len() as u64,
            content: template.content.filter(|_| chunk == 0),
            text: rest[..end].to_string(),
            ..template.clone()
        });
        rest = &rest[end..];
        if rest.is_empty() {
            break;
        }
    }
    records
}

#[test]
fn a_head_collected_chunk_by_chunk_is_the_whole_piece_head_however_it_was_cut() {
    let mut rng = Rng::new(60_000);
    for round in 0..300 {
        let text = if round % 3 == 0 {
            generate::long_text(&mut rng)
        } else {
            generate::text(&mut rng, 3_000)
        };
        let records = cut(&text, &mut rng);
        let limit = 1 + rng.below(4_000) as usize;
        let read = RecordRead {
            heads: vec![(Kind::ToolResult, limit)],
            ..RecordRead::default()
        };
        let answered = items(&read, &records);
        if encoded_len(&text) > 2 * limit {
            assert_eq!(answered.len(), 1, "round {round}");
            let (
                ReadItem::Record {
                    record,
                    tail: Some(back),
                    truncated: true,
                    ..
                },
                next,
            ) = &answered[0]
            else {
                panic!("round {round}: a head");
            };
            assert_eq!(
                (record.text.as_str(), back.as_str()),
                (head(&text, limit), tail(&text, limit)),
                "round {round}"
            );
            assert_eq!(
                (record.chunk, record.size, *next),
                (0, text.len() as u64, records.len() as u64)
            );
        } else {
            let whole: Vec<TurnRecord> = answered
                .into_iter()
                .map(|(item, _)| match item {
                    ReadItem::Record {
                        record,
                        tail: None,
                        truncated: false,
                        ..
                    } => record,
                    other => panic!("round {round}: {other:?}"),
                })
                .collect();
            assert_eq!(whole, records, "round {round}: a short piece whole");
        }
    }
}

#[test]
fn kinds_select_and_a_piece_fetch_stops_at_its_piece() {
    let turn = [
        message(&json!({"role": "user", "content": "q"})),
        message(&json!({"role": "tool", "content": "y".repeat(140_000), "tool_call_id": "t"})),
        message(&json!({"role": "assistant", "content": "a"})),
    ];
    let records = records_of(&turn).unwrap();
    assert_eq!(records.len(), 5);
    let only = |kinds: Vec<Kind>| {
        let read = RecordRead {
            kinds: Some(kinds),
            ..RecordRead::default()
        };
        items(&read, &records)
            .into_iter()
            .map(|(_, next)| next)
            .collect::<Vec<_>>()
    };
    assert_eq!(only(vec![Kind::Prompt, Kind::Reply]), [1, 5]);
    assert_eq!(only(vec![]), Vec::<u64>::new());
    let fetch = RecordRead {
        turn: Some(7),
        seq: Some(2),
        piece: true,
        ..RecordRead::default()
    };
    let mut viewer = Viewer::new(&fetch, 7, 1);
    let fetched: Vec<u64> = records
        .iter()
        .flat_map(|r| viewer.push(r))
        .map(|(_, next)| next)
        .collect();
    assert_eq!(fetched, [2, 3, 4], "the piece of records 1 to 3");
    assert!(viewer.done());
    let mid = RecordRead {
        turn: Some(7),
        seq: Some(3),
        piece: true,
        ..RecordRead::default()
    };
    let mut viewer = Viewer::new(&mid, 7, 3);
    let rest: Vec<u64> = records
        .iter()
        .flat_map(|r| viewer.push(r))
        .map(|(_, next)| next)
        .collect();
    assert_eq!(rest, [4], "a continuation inside the piece");
}

#[test]
fn a_read_checks_its_fields_and_binds_its_continuation() {
    let bad = |read: RecordRead| read.check().unwrap_err();
    assert!(
        bad(RecordRead {
            turn: Some(1),
            after: 1,
            ..RecordRead::default()
        })
        .contains("excludes")
    );
    assert!(
        bad(RecordRead {
            seq: Some(1),
            ..RecordRead::default()
        })
        .contains("seq requires turn")
    );
    assert!(
        bad(RecordRead {
            turn: Some(1),
            piece: true,
            ..RecordRead::default()
        })
        .contains("piece requires")
    );
    assert!(
        bad(RecordRead {
            heads: vec![(Kind::Reply, 32_769)],
            ..RecordRead::default()
        })
        .contains("1 to 32768")
    );
    assert!(
        bad(RecordRead {
            page_bytes: 0,
            ..RecordRead::default()
        })
        .contains("page_bytes")
    );
    let read = RecordRead {
        kinds: Some(vec![Kind::Reply, Kind::Prompt]),
        ..RecordRead::default()
    };
    let same = RecordRead {
        kinds: Some(vec![Kind::Prompt, Kind::Reply]),
        page_bytes: 7,
        ..RecordRead::default()
    };
    assert_eq!(
        read.fingerprint("c"),
        same.fingerprint("c"),
        "kinds sorted, the page size unbound"
    );
    let other = RecordRead {
        kinds: Some(vec![Kind::Prompt]),
        ..RecordRead::default()
    };
    let position = crate::conversation::turn_records::ReadPosition {
        turn: 3,
        seq: 9,
        log_offset: 10,
        log_line: 2,
        record_offset: 77,
    };
    let token = position.encode(&read.fingerprint("c"));
    assert_eq!(
        crate::conversation::turn_records::ReadPosition::decode(&token, &same.fingerprint("c")),
        Ok(position)
    );
    let refused =
        crate::conversation::turn_records::ReadPosition::decode(&token, &other.fingerprint("c"))
            .unwrap_err();
    assert_eq!(refused, "page continuation belongs to a different request");
}
