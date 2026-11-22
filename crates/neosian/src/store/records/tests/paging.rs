//! Record reads on a FileStore, continued: pages keep their bound and
//! continuations resume exactly, open drafts stay invisible, a converted
//! turn past the id bound is refused by name, and memory follows the page.

use serde_json::json;

use super::memory::peak;
use super::reading::{SESSION, joined, mixed, pages, views};
use super::{enabled, messages, part, written};
use crate::conversation::turn_records::{Kind, ReadItem, ReadPosition, RecordRead, records_of};

#[test]
fn pages_keep_their_bound_and_continuations_resume_exactly() {
    let (_home, store) = mixed("records-read-pages");
    for read in views() {
        let whole = joined(
            pages(
                &store,
                &RecordRead {
                    page_bytes: 64 << 20,
                    ..read.clone()
                },
            )
            .unwrap(),
        );
        for page_bytes in [1, 300, 5_000, 70_000, 200_000, 983_040] {
            let paged = pages(
                &store,
                &RecordRead {
                    page_bytes,
                    ..read.clone()
                },
            )
            .unwrap();
            for page in &paged {
                let bytes: u64 = page.iter().map(ReadItem::bytes).sum();
                let one_group =
                    page.len() == 1 || (page.len() == 2 && matches!(page[0], ReadItem::Header(_)));
                assert!(
                    bytes <= page_bytes || one_group,
                    "a page of {bytes} bytes for {page_bytes}"
                );
            }
            assert_eq!(
                joined(paged),
                whole,
                "page_bytes {page_bytes}, {:?}",
                read.kinds
            );
        }
    }
    let mut read = RecordRead {
        page_bytes: 300,
        ..RecordRead::default()
    };
    let next = store
        .read_records(SESSION, &read, &mut |_| Ok(()))
        .unwrap()
        .unwrap();
    read.continuation = Some(next.encode(&read.fingerprint(SESSION)));
    let other = RecordRead {
        kinds: Some(vec![Kind::Reply]),
        ..read.clone()
    };
    let refused = store
        .read_records(SESSION, &other, &mut |_| Ok(()))
        .unwrap_err()
        .text();
    assert_eq!(refused, "page continuation belongs to a different request");
    let stale = ReadPosition {
        log_offset: 5,
        log_line: 3,
        record_offset: 9,
        ..next
    };
    read.continuation = Some(stale.encode(&read.fingerprint(SESSION)));
    let mut items = 0;
    store
        .read_records(SESSION, &read, &mut |_| {
            items += 1;
            Ok(())
        })
        .unwrap();
    assert!(
        items > 0,
        "a hint that does not hold is read past from the start"
    );
}

#[test]
fn open_drafts_stay_invisible_and_a_converted_turn_past_the_id_bound_is_refused() {
    let (_home, store) = mixed("records-read-drafts");
    let draft = records_of(&messages(&[json!({"role": "user", "content": "unsealed"})])).unwrap();
    part(&store, SESSION, "open", draft, false).unwrap();
    let mut turns = Vec::new();
    store
        .read_records(
            SESSION,
            &RecordRead {
                kinds: Some(Vec::new()),
                ..RecordRead::default()
            },
            &mut |item| {
                if let ReadItem::Header(header) = item {
                    turns.push(header.turn);
                }
                Ok(())
            },
        )
        .unwrap();
    assert_eq!(turns, [1, 2, 3, 4]);
    let long = messages(&[
        json!({"role": "assistant", "tool_calls": [{"id": "i".repeat(1_025), "name": "n"}]}),
    ]);
    store.append_turn(SESSION, long, None).unwrap();
    let refused = store
        .read_records(
            SESSION,
            &RecordRead {
                turn: Some(5),
                ..RecordRead::default()
            },
            &mut |_| Ok(()),
        )
        .unwrap_err();
    assert!(
        refused
            .text()
            .contains("agent_conversation_format_unsupported")
            && refused.text().contains("longer than 1024 bytes"),
        "{}",
        refused.text()
    );
    assert_eq!(
        store.read_turns(SESSION, 4, None).unwrap().len(),
        1,
        "read_turns still answers it whole"
    );
}

/// The peak of each page of one turn's full view, the turn a `size`-byte tool result.
fn read_peaks(name: &str, size: usize) -> Vec<usize> {
    let (_home, store) = enabled(name);
    let result = format!("{}\n", "r".repeat(99)).repeat(size / 100);
    written(
        &store,
        SESSION,
        "d",
        &messages(&[
            json!({"role": "user", "content": "go"}),
            json!({"role": "tool", "content": result, "tool_call_id": "t"}),
        ]),
    );
    let mut read = RecordRead::default();
    let mut peaks = Vec::new();
    loop {
        let (next, held) = peak(|| store.read_records(SESSION, &read, &mut |_| Ok(())).unwrap());
        peaks.push(held);
        match next {
            Some(position) => read.continuation = Some(position.encode(&read.fingerprint(SESSION))),
            None => return peaks,
        }
    }
}

#[test]
fn memory_during_a_read_follows_the_page_not_the_turn() {
    let small = read_peaks("records-read-memory-small", 1 << 20);
    let large = read_peaks("records-read-memory-large", 16 << 20);
    let (typical, worst) = (*small.iter().max().unwrap(), *large.iter().max().unwrap());
    eprintln!(
        "read peaks: 1 MiB turn {small:?}; 16 MiB turn {} pages, most {worst}",
        large.len()
    );
    assert!(large.len() >= 16, "{} pages", large.len());
    assert!(
        worst <= typical + (128 << 10),
        "a 16 MiB turn's page held {worst} bytes, a 1 MiB turn's {typical}"
    );
    assert!(worst < 1 << 20, "a page held {worst} bytes");
}
