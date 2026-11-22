//! A turn's search facts from its records (§40.54 item 15.1, T1b2a1 note):
//! over generated turns, cut however a client cuts them, each term's count
//! and the folded length equal what the ranking takes from the turn's whole
//! searchable text; the one stated difference is a word-final capital sigma.

use super::generate::{self, Rng};
use super::message;
use crate::conversation::message::Message;
use crate::conversation::search::messages_text;
use crate::conversation::turn_records::{
    TEXT_BYTES, TextFacts, TurnRecord, encoded_len, records_of,
};
use crate::text::is_space;

/// What `Ranking::offer` counts: each term's non-overlapping matches in the
/// folded text, and the folded length.
fn whole(messages: &[Message], terms: &[String]) -> (Vec<u32>, u64) {
    let folded = messages_text(messages).to_lowercase();
    let counts = terms
        .iter()
        .map(|term| folded.matches(term.as_str()).count() as u32)
        .collect();
    (counts, folded.chars().count() as u64)
}

fn streamed(records: &[TurnRecord], terms: &[String]) -> (Vec<u32>, u64) {
    let mut facts = TextFacts::new(terms);
    records.iter().for_each(|record| facts.push(record));
    facts.finish()
}

/// Terms as a query gives them: lowercased, free of white space, some taken
/// from the text itself so that they match, some overlapping themselves.
fn terms(rng: &mut Rng, text: &str) -> Vec<String> {
    let folded: Vec<char> = text.to_lowercase().chars().collect();
    let mut terms = vec!["aa".to_string(), "a".to_string(), "{\"".to_string()];
    for _ in 0..4 {
        if folded.is_empty() {
            break;
        }
        let start = rng.below(folded.len() as u64) as usize;
        let term: String = folded[start..]
            .iter()
            .take(1 + rng.below(6) as usize)
            .take_while(|ch| !is_space(**ch))
            .collect();
        if !term.is_empty() {
            terms.push(term);
        }
    }
    terms.sort();
    terms.dedup();
    terms
}

/// The records again, each piece cut into small chunks wherever it falls.
fn recut(records: &[TurnRecord], rng: &mut Rng) -> Vec<TurnRecord> {
    let mut out: Vec<TurnRecord> = Vec::new();
    let mut at = 0;
    while at < records.len() {
        let end = (at + 1..records.len())
            .find(|i| records[*i].chunk == 0)
            .unwrap_or(records.len());
        let text: String = records[at..end].iter().map(|r| r.text.as_str()).collect();
        let (mut rest, mut chunk) = (text.as_str(), 0);
        loop {
            let mut cut = if rest.is_empty() || encoded_len(rest) <= TEXT_BYTES && rng.one_in(3) {
                rest.len()
            } else {
                1 + rng.below(rest.len().min(4_000) as u64) as usize
            };
            while !rest.is_char_boundary(cut) {
                cut += 1;
            }
            out.push(TurnRecord {
                seq: out.len() as u64,
                chunk,
                content: records[at].content.filter(|_| chunk == 0),
                text: rest[..cut].to_string(),
                ..records[at].clone()
            });
            (rest, chunk) = (&rest[cut..], chunk + 1);
            if rest.is_empty() {
                break;
            }
        }
        at = end;
    }
    out
}

#[test]
fn facts_from_records_equal_the_whole_texts_however_the_records_are_cut() {
    for seed in 0..1_500 {
        let mut rng = Rng::new(70_000 + seed);
        let messages: Vec<Message> = (0..=rng.below(4))
            .map(|_| message(&generate::message(&mut rng, seed % 60 == 0)))
            .collect();
        let terms = terms(&mut rng, &messages_text(&messages));
        let records = records_of(&messages).unwrap();
        let expected = whole(&messages, &terms);
        assert_eq!(
            streamed(&records, &terms),
            expected,
            "seed {seed}: {terms:?}"
        );
        assert_eq!(
            streamed(&recut(&records, &mut rng), &terms),
            expected,
            "seed {seed}: recut"
        );
    }
}

#[test]
fn separators_blocks_and_calls_are_rebuilt_as_the_searchable_text() {
    let messages: Vec<Message> = [
        serde_json::json!({"role": "user", "content": [{"type": "text", "text": ""}, {"type": "image", "url": "u"}, {"type": "text", "text": "b"}]}),
        serde_json::json!({"role": "user", "content": [{"type": "text", "text": ""}]}),
        serde_json::json!({"role": "assistant", "content": "", "tool_calls": [{"id": "c", "name": "Grep", "arguments": {"q": "x y"}}]}),
        serde_json::json!({"role": "tool", "content": "ok", "tool_call_id": "c", "reasoning": "hidden", "extra": {"k": "v"}}),
    ]
    .iter()
    .map(message)
    .collect();
    assert_eq!(messages_text(&messages), "\nb\nGrep {\"q\":\"x y\"}\nok");
    let terms: Vec<String> = ["\nb", "grep", "{\"q\"", "hidden", "v", "ok"]
        .map(String::from)
        .to_vec();
    let records = records_of(&messages).unwrap();
    assert_eq!(streamed(&records, &terms), whole(&messages, &terms));
    assert_eq!(
        streamed(&records, &terms).0,
        [1, 1, 1, 0, 0, 1],
        "reasoning and extras are not searched"
    );
    let sigma = records_of(&[message(
        &serde_json::json!({"role": "user", "content": "ΟΔΟΣ"}),
    )])
    .unwrap();
    let final_sigma = vec!["οδος".to_string(), "οδοσ".to_string()];
    assert_eq!(
        streamed(&sigma, &final_sigma).0,
        [0, 1],
        "the stated difference: σ, where the whole fold gives ς"
    );
}
