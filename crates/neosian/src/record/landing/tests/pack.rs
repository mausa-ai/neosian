//! The 983,040-byte promise for write parts (DESIGN §40.54 item 8.2):
//! bodies measured with `encode::write`, the bytes the client sends, at the
//! exact boundary and over generated turns. The bounds are written here as
//! literals, never read from `pack.rs`.

use serde_json::{Value, json};

use super::super::pack::{Draft, Parts, turn_bytes};
use super::ID;
use crate::conversation::message::{self, Message, Role};
use crate::conversation::turn_records::{Form, Kind, TurnRecord, Write, records_of};
use crate::remote::records::encode;

const DRAFT: &str = "p0123456789abcdef0123456789abcdef";

fn draft<'a>(actor: &'a str, records: &[TurnRecord]) -> Draft<'a> {
    Draft {
        conversation: ID,
        draft: DRAFT,
        actor,
        turn_bytes: turn_bytes(records),
    }
}

fn body(write: &Write) -> usize {
    encode::write(ID, write).len()
}

/// Every part of `records`, refused parts included.
fn parts(draft: &Draft, records: &[TurnRecord]) -> Vec<Result<Write, String>> {
    Parts::new(draft, records.to_vec(), true).collect()
}

fn prompt(seq: u64, length: usize) -> TurnRecord {
    TurnRecord {
        seq,
        message: seq,
        role: Role::User,
        kind: Kind::Prompt,
        content: Some(Form::Text),
        block: None,
        call: None,
        call_id: None,
        name: None,
        chunk: 0,
        size: length as u64,
        text: "a".repeat(length),
    }
}

/// Sixteen prompts and a seventeenth when `more`, the sixteenth sized so
/// that a part holding the first sixteen, opening and sealing when
/// `sealing`, has a body of exactly `target` bytes by `encode::write`.
/// Every text stays within a record's 65,536 encoded bytes.
fn sized(target: usize, sealing: bool, more: bool) -> Vec<TurnRecord> {
    let mut records: Vec<TurnRecord> = (0..16).map(|seq| prompt(seq, 61_300)).collect();
    if more {
        records.push(prompt(16, 10));
    }
    loop {
        let actor = format!("claude-code:{ID}");
        let draft = draft(&actor, &records);
        let have = body(&draft.part(records[..16].to_vec(), true, sealing));
        if have == target {
            assert!(records.iter().all(|r| r.check().is_ok()));
            return records;
        }
        let length = (records[15].text.len() + target).checked_sub(have).unwrap();
        records[15] = prompt(15, length);
    }
}

fn counts(parts: &[Result<Write, String>]) -> Vec<(usize, bool)> {
    let part = |p: &Result<Write, String>| {
        let write = p.as_ref().unwrap();
        (write.records.len(), write.seal)
    };
    parts.iter().map(part).collect()
}

#[test]
fn a_part_ends_before_the_record_that_would_make_it_983041_bytes() {
    let actor = format!("claude-code:{ID}");
    for sealing in [false, true] {
        // One more record would make the first part exactly 983,041 bytes.
        let records = sized(983_041, sealing, !sealing);
        let found = parts(&draft(&actor, &records), &records);
        let expected = if sealing {
            vec![(15, false), (1, true)]
        } else {
            vec![(15, false), (2, true)]
        };
        assert_eq!(counts(&found), expected);
        for part in &found {
            assert!(body(part.as_ref().unwrap()) <= 983_040);
        }
    }
}

#[test]
fn a_part_of_exactly_983040_bytes_is_sent_whole() {
    let actor = format!("claude-code:{ID}");
    for sealing in [true, false] {
        let records = sized(983_040, sealing, !sealing);
        let found = parts(&draft(&actor, &records), &records);
        let first = found[0].as_ref().unwrap();
        assert_eq!((first.records.len(), body(first)), (16, 983_040));
        let expected = if sealing {
            vec![(16, true)]
        } else {
            vec![(16, false), (1, true)]
        };
        assert_eq!(counts(&found), expected);
    }
}

#[test]
fn a_part_carries_at_most_2048_records() {
    let records: Vec<TurnRecord> = (0..2_049).map(|seq| prompt(seq, 0)).collect();
    let found = parts(&draft("a:b", &records), &records);
    assert_eq!(counts(&found), [(2_048, false), (1, true)]);
}

#[test]
fn a_part_over_the_bound_is_refused_before_it_is_handed_out() {
    // An actor no hook can name: the opening part cannot fit its record.
    let actor = "a:".to_string() + &"x".repeat(983_040);
    let records = vec![prompt(0, 3)];
    let draft = draft(&actor, &records);
    let bytes = body(&draft.part(records.clone(), true, true));
    assert!(bytes > 983_040);
    let mut found = Parts::new(&draft, records, true);
    let refused = found.next().unwrap().unwrap_err();
    assert!(
        refused.starts_with(&format!(
            "a part of this turn would carry {bytes} bytes in 1 records"
        )),
        "{refused}"
    );
    assert!(found.next().is_none());
}

/// splitmix64, so every run generates the same turns.
struct Rng(u64);

impl Rng {
    fn below(&mut self, bound: u64) -> u64 {
        self.0 = self.0.wrapping_add(0x9e37_79b9_7f4a_7c15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        (z ^ (z >> 31)) % bound.max(1)
    }
}

/// Characters for how they encode: escapes, control characters, multi-byte.
const CHARS: &[char] = &[
    'a', ' ', '\n', '"', '\\', '\u{1}', '\u{1f}', 'é', '中', '😀',
];

fn text(rng: &mut Rng, most: u64) -> String {
    let length = rng.below(most + 1);
    (0..length)
        .map(|_| CHARS[rng.below(CHARS.len() as u64) as usize])
        .collect()
}

fn id(rng: &mut Rng) -> String {
    match rng.below(4) {
        0 => "i".repeat(1_024),
        1 => "é".repeat(512),
        _ => text(rng, 12),
    }
}

/// A recorder's turn: a prompt, tool rounds, a reply; empty pieces, long
/// ids and names, escaped and multi-byte text, results past one record.
fn turn(rng: &mut Rng) -> Vec<Message> {
    let mut messages = vec![Message::text(Role::User, text(rng, 40))];
    for _ in 0..rng.below(12) {
        let call = json!({"role": "assistant", "tool_calls": [{
            "id": id(rng), "name": id(rng), "arguments": {"input": text(rng, 30)}}]});
        messages.push(message::from_json(&call).unwrap());
        let call_id = messages.last().unwrap().tool_calls[0].id.clone();
        let result = match rng.below(4) {
            0 => String::new(),
            1 => text(rng, 300_000),
            _ => text(rng, 2_000),
        };
        let result: Value = json!({"role": "tool", "content": result, "tool_call_id": call_id});
        messages.push(message::from_json(&result).unwrap());
    }
    messages.push(Message::text(Role::Assistant, text(rng, 40)));
    messages
}

/// Every property of a turn's parts, its records from `records_of`; the
/// number of parts.
fn holds(messages: &[Message], actor: &str) -> usize {
    let records = records_of(messages).unwrap();
    let draft = draft(actor, &records);
    let found: Vec<Write> = parts(&draft, &records)
        .into_iter()
        .map(Result::unwrap)
        .collect();
    let mut seq = 0;
    let mut joined = Vec::new();
    for (at, part) in found.iter().enumerate() {
        let last = at + 1 == found.len();
        assert!(body(part) <= 983_040 && part.records.len() <= 2_048);
        assert!(!part.records.is_empty());
        assert_eq!(part.seal, last);
        assert_eq!(part.turn_bytes.is_some(), at == 0);
        assert_eq!(part.actor.as_deref(), (at == 0).then_some(actor));
        for record in &part.records {
            assert_eq!(record.seq, seq);
            seq += 1;
        }
        if !last {
            // The part could not have taken the next record.
            let mut grown = part.records.clone();
            grown.push(found[at + 1].records[0].clone());
            let wider = draft.part(grown, at == 0, false);
            assert!(body(&wider) > 983_040 || wider.records.len() > 2_048);
        }
        joined.extend(part.records.iter().cloned());
    }
    assert_eq!(joined, records);
    // The sealed header's `bytes`: every piece's text, its chunks joined.
    let header: u64 = records.iter().map(|r| r.text.len() as u64).sum();
    assert_eq!(found[0].turn_bytes, Some(header));
    found.len()
}

#[test]
fn every_part_of_a_generated_turn_keeps_the_bounds_and_the_records() {
    let mut rng = Rng(0x7431_6531);
    let actors = [
        "a:b".to_string(),
        format!("claude-code:{}", "s".repeat(1_000)),
        format!("x:{}", "é".repeat(100_000)),
    ];
    let mut split = 0;
    for round in 0..40 {
        let messages = turn(&mut rng);
        split += usize::from(holds(&messages, &actors[round % actors.len()]) > 1);
    }
    assert!(split >= 10, "{split} of 40 turns took more than one part");
    // 2,049 tiny records, and one tool result of 17 MiB.
    let tiny = (0..2_049).map(|_| Message::text(Role::User, String::new()));
    assert_eq!(holds(&tiny.collect::<Vec<_>>(), &actors[0]), 2);
    let mut large = turn(&mut Rng(16));
    let result = "0123456789abcdé\n".repeat(1 << 20);
    large.push(Message::text(Role::Tool, result));
    assert!(holds(&large, &actors[1]) >= 18);
}
