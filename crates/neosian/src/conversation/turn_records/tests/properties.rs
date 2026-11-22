//! Properties over generated messages: every shape round-trips byte for
//! byte through records; every record stays within its bounds; the
//! conversion is deterministic; any split within the bounds rebuilds the
//! same messages; any float reads back as a format-1 line reads it back.

use std::collections::BTreeSet;

use serde_json::{Value, json};

use super::generate::{self, Rng};
use super::{message, spelled};
use crate::conversation::message::{self as codec, Message, Role};
use crate::conversation::turn_records::{
    Form, Kind, TEXT_BYTES, TurnRecord, encoded_len, messages_of, records_of,
};
use crate::pyfmt::dumps;

fn turn(rng: &mut Rng, big: bool) -> Vec<Message> {
    (0..=rng.below(4))
        .map(|_| message(&generate::message(rng, big)))
        .collect()
}

fn spelled_all(messages: &[Message]) -> Vec<String> {
    messages.iter().map(spelled).collect()
}

/// What every record of a converted turn holds to (§40.54 items 3.1, 3.4).
fn bounded(records: &[TurnRecord]) {
    for (at, record) in records.iter().enumerate() {
        assert_eq!(record.seq, at as u64);
        record.check().expect("a record within its bounds");
        assert!(encoded_len(&record.text) <= TEXT_BYTES);
        assert!(!record.text.is_empty() || record.size == 0);
        let opens = at == 0 || records[at - 1].message != record.message;
        assert_eq!(
            record.content.is_some(),
            opens,
            "content on a message's first record only"
        );
        if opens {
            let expected = if at == 0 {
                0
            } else {
                records[at - 1].message + 1
            };
            assert_eq!(record.message, expected, "message indexes dense from 0");
        }
    }
}

#[test]
fn every_message_shape_round_trips_byte_for_byte() {
    let (mut kinds, mut chunked, mut placeholders, mut carried) = (BTreeSet::new(), 0, 0, 0);
    for seed in 0..2_000 {
        let mut rng = Rng::new(seed);
        let messages = turn(&mut rng, seed % 40 == 0);
        let records = records_of(&messages).unwrap_or_else(|why| panic!("seed {seed}: {why}"));
        bounded(&records);
        kinds.extend(records.iter().map(|record| record.kind.as_str()));
        chunked += records.iter().filter(|record| record.chunk > 0).count();
        placeholders += records
            .iter()
            .filter(|r| {
                r.size == 0 && r.block.is_none() && r.content.is_some_and(|f| f != Form::Text)
            })
            .count();
        carried += records
            .iter()
            .filter(|r| r.content.is_some() && r.role != Role::Tool && r.kind != Kind::ToolCall)
            .filter(|r| r.call_id.is_some())
            .count();
        let rebuilt = messages_of(&records).unwrap_or_else(|why| panic!("seed {seed}: {why}"));
        assert_eq!(spelled_all(&rebuilt), spelled_all(&messages), "seed {seed}");
        assert_eq!(
            records_of(&rebuilt).unwrap(),
            records,
            "seed {seed}: deterministic"
        );
        for record in &records {
            let (turn, back) = TurnRecord::from_json(&record.to_json(Some(9))).unwrap();
            assert_eq!((turn, &back), (Some(9), record), "seed {seed}");
            let (none, written) = TurnRecord::from_json(&record.to_json(None)).unwrap();
            assert_eq!((none, &written), (None, record), "seed {seed}");
        }
    }
    assert_eq!(
        kinds.len(),
        Kind::ALL.len(),
        "every kind was generated: {kinds:?}"
    );
    assert!(
        chunked > 100 && placeholders > 50 && carried > 50,
        "{chunked} {placeholders} {carried}"
    );
}

/// The chunks of each piece cut again at random places within the bound.
fn resplit(records: &[TurnRecord], rng: &mut Rng) -> Vec<TurnRecord> {
    let mut out: Vec<TurnRecord> = Vec::new();
    let mut start = 0;
    while start < records.len() {
        let head = &records[start];
        let end = (start + 1..records.len())
            .find(|at| records[*at].chunk == 0)
            .unwrap_or(records.len());
        let whole: String = records[start..end]
            .iter()
            .map(|r| r.text.as_str())
            .collect();
        let (mut rest, mut chunk) = (whole.as_str(), 0);
        loop {
            let all = rest.is_empty() || (encoded_len(rest) <= TEXT_BYTES && rng.one_in(2));
            let cut = if all {
                rest.len()
            } else {
                let (mut used, mut most) = (2, 0);
                for (at, ch) in rest.char_indices() {
                    used += encoded_len(&ch.to_string()) - 2;
                    if used > TEXT_BYTES {
                        break;
                    }
                    most = at + ch.len_utf8();
                }
                let mut cut = 1 + rng.below(most as u64) as usize;
                while !rest.is_char_boundary(cut) {
                    cut += 1;
                }
                cut
            };
            out.push(TurnRecord {
                seq: out.len() as u64,
                content: head.content.filter(|_| chunk == 0),
                chunk,
                text: rest[..cut].to_string(),
                ..head.clone()
            });
            (rest, chunk) = (&rest[cut..], chunk + 1);
            if rest.is_empty() {
                break;
            }
        }
        start = end;
    }
    out
}

#[test]
fn any_split_within_the_bounds_rebuilds_the_same_messages() {
    for seed in 0..300 {
        let mut rng = Rng::new(10_000 + seed);
        let messages = turn(&mut rng, seed % 10 == 0);
        let records = resplit(&records_of(&messages).unwrap(), &mut rng);
        let rebuilt = messages_of(&records).unwrap_or_else(|why| panic!("seed {seed}: {why}"));
        assert_eq!(spelled_all(&rebuilt), spelled_all(&messages), "seed {seed}");
    }
}

#[test]
fn any_float_reads_back_as_a_format_one_line_reads_it_back() {
    let mut rng = Rng::new(77);
    for _ in 0..2_000 {
        let float = loop {
            let float = f64::from_bits(rng.next());
            if float.is_finite() {
                break float;
            }
        };
        let sent = message(&json!({
            "role": "assistant", "reasoning": float,
            "tool_calls": [{"id": "c", "name": "n", "arguments": {"x": [float, -float]}}],
            "extra": {"f": float}
        }));
        let line = dumps(&codec::to_json(&sent), true);
        let format_one = message(&serde_json::from_str::<Value>(&line).unwrap());
        let records = messages_of(&records_of(std::slice::from_ref(&sent)).unwrap()).unwrap();
        assert_eq!(spelled(&records[0]), spelled(&format_one), "{float:e}");
    }
}
