//! Parts: a long result walked part by part, a record in the middle of a
//! piece opening the piece from its start, the last part named, what is
//! refused, and what the reads asked for: never an opaque kind, part `k`
//! from its own record.

use super::fake::Paged;
use super::{LABEL, agreed, ask, records, result_text, stores};
use crate::conversation::recall::recall;
use crate::conversation::turn_records::{Kind, TEXT_BYTES, encoded_len};

/// The stored chunks of the piece starting at `seq`.
fn chunks(seq: u64) -> Vec<String> {
    let all = records();
    let first = all.iter().find(|r| r.seq == seq).unwrap();
    all.iter()
        .filter(|r| {
            (r.message, r.kind, r.block, r.call)
                == (first.message, first.kind, first.block, first.call)
        })
        .map(|r| r.text.clone())
        .collect()
}

fn first_seq(kind: Kind) -> u64 {
    records()
        .iter()
        .find(|r| r.kind == kind && r.chunk == 0)
        .unwrap()
        .seq
}

#[test]
fn a_long_result_is_walked_part_by_part_each_one_stored_chunk() {
    let stores = stores("recall-walk");
    let start = first_seq(Kind::ToolResult);
    let stored = chunks(start);
    assert!(stored.len() >= 4);
    let grouped = super::super::grouped(result_text().len() as u64);
    let mut walked = String::new();
    for (part, chunk) in stored.iter().enumerate() {
        let asked = ask(None, Some("t1"), None, Some(part as i64));
        let recalled = agreed(&stores, &asked).unwrap();
        assert!(!recalled.view);
        let mut lines: Vec<&str> = recalled.text.split('\n').collect();
        let header = if part == 0 {
            "Turn 1, call t1 (verbatim):".to_string()
        } else {
            format!("Turn 1, call t1, part {part} (verbatim):")
        };
        assert_eq!(lines.remove(0), header);
        if part == 0 {
            let args = lines.remove(0);
            assert!(args.starts_with("AGENT calls Read({\"path\":\"/a b\",\"blob\":\"AAA"));
            assert!(args.contains(" bytes omitted; pass seq=2 to read it in parts] … AAA"));
            assert!(args.len() < 1_400, "{}", args.len());
        }
        let marker = lines.pop().unwrap();
        let text = lines.join("\n");
        let text = text.strip_prefix("TOOL Read → ").unwrap();
        assert_eq!(text, chunk);
        assert!(encoded_len(text) <= TEXT_BYTES);
        walked.push_str(text);
        if part + 1 < stored.len() {
            let more = format!(
                "… [part {part} of a result of {grouped} bytes; pass part={} to continue]",
                part + 1
            );
            assert_eq!(marker, more);
        } else {
            let last = format!("… [part {part}, the last, of a result of {grouped} bytes]");
            assert_eq!(marker, last);
        }
    }
    assert_eq!(walked, result_text());
    let past = ask(None, Some("t1"), None, Some(stored.len() as i64));
    let refused = agreed(&stores, &past).unwrap_err();
    let error = format!(
        "Part {} is past the end of a result of {grouped} bytes in turn 1",
        stored.len()
    );
    assert_eq!(refused.error, error);
}

#[test]
fn a_record_in_the_middle_opens_its_piece_from_the_beginning() {
    let stores = stores("recall-seq");
    let start = first_seq(Kind::ToolResult);
    let stored = chunks(start);
    let middle = (start + 2) as i64;
    let opened = agreed(&stores, &ask(None, None, Some(middle), None)).unwrap();
    let head = format!(
        "Turn 1, seq {start} (verbatim):\nTOOL Read → {}\n… [part 0 of",
        stored[0]
    );
    assert!(opened.text.starts_with(&head));
    let second = agreed(&stores, &ask(None, None, Some(middle), Some(2))).unwrap();
    let head = format!(
        "Turn 1, seq {start}, part 2 (verbatim):\nTOOL Read → {}\n",
        stored[2]
    );
    assert!(second.text.starts_with(&head));
    let prompt = agreed(&stores, &ask(None, None, Some(0), None)).unwrap();
    assert_eq!(
        prompt.text,
        "Turn 1, seq 0 (verbatim):\nUSER: Review café \"memory\"\nnow"
    );
    let call = agreed(&stores, &ask(None, None, Some(3), Some(1))).unwrap();
    assert!(
        call.text
            .starts_with("Turn 1, seq 2, part 1 (verbatim):\nAGENT calls Read: AAAA")
    );
}

#[test]
fn opaque_records_unknown_selectors_and_missing_results_are_refused() {
    let stores = stores("recall-refused");
    let reasoning = first_seq(Kind::Reasoning) as i64;
    let hidden = agreed(&stores, &ask(None, None, Some(reasoning), None)).unwrap_err();
    assert_eq!(
        hidden.error,
        format!("Record {reasoning} of turn 1 is not one recall shows")
    );
    assert!(!format!("{hidden:?}").contains("REASONING-CANARY"));
    let count = records().len();
    let beyond = agreed(&stores, &ask(None, None, Some(99), None)).unwrap_err();
    assert_eq!(beyond.error, "Turn 1 has no record 99");
    let hint = format!("Its records are 0 to {}.", count - 1);
    assert_eq!(beyond.hint.as_deref(), Some(hint.as_str()));
    let unanswered = agreed(&stores, &ask(None, Some("t2"), None, None)).unwrap_err();
    assert_eq!(unanswered.error, "Turn 1's call 't2' has no result");
    let unknown = agreed(&stores, &ask(None, Some("nope"), None, None)).unwrap_err();
    assert_eq!(unknown.error, "Turn 1 made no call 'nope'");
    assert_eq!(unknown.hint.as_deref(), Some("Its calls: t1, t2."));
}

#[test]
fn every_read_names_visible_kinds_and_a_part_starts_at_its_own_record() {
    let stores = stores("recall-asked");
    let paged = Paged::new(records());
    let start = first_seq(Kind::ToolResult);
    let asks = [
        ask(None, None, None, None),
        ask(Some(super::View::Prompts), None, None, None),
        ask(Some(super::View::Calls), None, None, None),
        ask(Some(super::View::Full), None, None, None),
        ask(None, Some("t1"), None, Some(3)),
        ask(None, None, Some((start + 1) as i64), Some(2)),
        ask(None, None, Some(first_seq(Kind::Reasoning) as i64), None),
    ];
    for asked in &asks {
        let _ = recall(&stores.legacy, Some(&paged), asked, LABEL).unwrap();
    }
    let opaque = [Kind::Reasoning, Kind::Attachment, Kind::Extra];
    for read in paged.asked.borrow().iter() {
        let kinds = read.kinds.as_ref().expect("a view names its kinds");
        assert!(kinds.iter().all(|kind| !opaque.contains(kind)), "{read:?}");
        assert_eq!(read.turn, Some(1));
    }
    let parts: Vec<Option<u64>> = paged.asked.borrow().iter().map(|read| read.seq).collect();
    assert!(parts.contains(&Some(start + 3)), "{parts:?}");
    assert!(parts.contains(&Some(start + 2)), "{parts:?}");
    let light = &paged.asked.borrow()[0];
    assert!(
        light
            .heads
            .iter()
            .any(|(kind, _)| *kind == Kind::ToolResult)
    );
}
