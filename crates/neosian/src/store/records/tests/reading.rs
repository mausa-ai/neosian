//! Record reads on a FileStore (§40.54 item 10): over one conversation of
//! a format-1 turn, a redacted skeleton, a sealed format-2 turn and another
//! format-1 turn, every view equals the codec's records; a head states the
//! full size and a piece fetch returns the whole; pages keep their bound
//! and continuations resume exactly; memory follows the page.

use serde_json::json;

use super::{enabled, example, messages, written};
use crate::conversation::message::Message;
use crate::conversation::turn_records::{
    Header, Kind, ReadItem, RecordRead, TurnRecord, head, records_of, tail,
};
use crate::conversation::{Error, Turn};
use crate::store::FileStore;
use crate::store::time::Stamp;

pub(super) const SESSION: &str = "c-1";

fn small() -> Vec<Message> {
    messages(&[
        json!({"role": "user", "content": "short"}),
        json!({"role": "assistant", "content": "done"}),
    ])
}

/// Turns 1 (whole), 2 (redacted), 3 (records) and 4 (whole).
pub(super) fn mixed(name: &str) -> (crate::testing::Scratch, FileStore) {
    let (home, store) = enabled(name);
    let stamp = Stamp::parse("2026-11-21T10:00:00Z").unwrap();
    let whole = Turn {
        conversation_id: SESSION.into(),
        turn: 1,
        messages: example(),
        created_at: stamp,
        actor: Some("cli:t".into()),
        redacted: false,
    };
    let skeleton = Turn {
        conversation_id: SESSION.into(),
        turn: 2,
        messages: Vec::new(),
        created_at: stamp,
        actor: None,
        redacted: true,
    };
    store
        .restore_conversation(SESSION, &[whole, skeleton], &[], &[])
        .unwrap();
    written(&store, SESSION, "d", &example());
    store.append_turn(SESSION, small(), None).unwrap();
    (home, store)
}

/// Every page of a read, each page's items, following its continuations.
pub(super) fn pages(store: &FileStore, read: &RecordRead) -> Result<Vec<Vec<ReadItem>>, Error> {
    let mut read = read.clone();
    let mut all = Vec::new();
    loop {
        let mut page = Vec::new();
        let next = store.read_records(SESSION, &read, &mut |item| {
            page.push(item);
            Ok(())
        })?;
        all.push(page);
        match next {
            Some(position) => read.continuation = Some(position.encode(&read.fingerprint(SESSION))),
            None => return Ok(all),
        }
        assert!(all.len() < 10_000, "pages move on");
    }
}

/// The items of all pages, a header repeated at a page's start dropped.
pub(super) fn joined(pages: Vec<Vec<ReadItem>>) -> Vec<ReadItem> {
    let mut items: Vec<ReadItem> = Vec::new();
    for item in pages.into_iter().flatten() {
        if items.contains(&item) && matches!(item, ReadItem::Header(_)) {
            continue;
        }
        items.push(item);
    }
    items
}

/// What a view of one turn answers, worked out from the codec's records.
fn expected(turn: u64, records: &[TurnRecord], read: &RecordRead) -> Vec<ReadItem> {
    let mut items = Vec::new();
    let mut at = 0;
    while at < records.len() {
        let end = (at + 1..records.len())
            .find(|i| records[*i].chunk == 0)
            .unwrap_or(records.len());
        let piece = &records[at..end];
        let text: String = piece.iter().map(|r| r.text.as_str()).collect();
        let kind = piece[0].kind;
        if read.selects(kind) {
            match read
                .head(kind)
                .filter(|n| crate::conversation::turn_records::encoded_len(&text) > 2 * n)
            {
                Some(n) => items.push(ReadItem::Record {
                    turn,
                    record: TurnRecord {
                        text: head(&text, n).into(),
                        ..piece[0].clone()
                    },
                    tail: Some(tail(&text, n).into()),
                    truncated: true,
                }),
                None => items.extend(piece.iter().map(|r| ReadItem::Record {
                    turn,
                    record: r.clone(),
                    tail: None,
                    truncated: false,
                })),
            }
        }
        at = end;
    }
    items
}

pub(super) fn views() -> Vec<RecordRead> {
    let kinds = |kinds: &[Kind]| Some(kinds.to_vec());
    vec![
        RecordRead::default(),
        RecordRead {
            kinds: kinds(&[Kind::Prompt, Kind::Reply]),
            ..RecordRead::default()
        },
        RecordRead {
            kinds: kinds(&[Kind::Prompt]),
            ..RecordRead::default()
        },
        RecordRead {
            kinds: kinds(&[Kind::Prompt, Kind::Reply, Kind::ToolCall]),
            heads: vec![(Kind::ToolCall, 2_048)],
            ..RecordRead::default()
        },
        RecordRead {
            heads: vec![(Kind::ToolResult, 4_096)],
            ..RecordRead::default()
        },
        RecordRead {
            kinds: kinds(&[]),
            ..RecordRead::default()
        },
    ]
}

#[test]
fn every_view_of_whole_and_sealed_turns_equals_the_codecs_records() {
    let (_home, store) = mixed("records-read-views");
    let sealed: Vec<Header> = {
        let mut headers = Vec::new();
        store
            .read_records(
                SESSION,
                &RecordRead {
                    kinds: Some(Vec::new()),
                    ..RecordRead::default()
                },
                &mut |item| {
                    if let ReadItem::Header(header) = item {
                        headers.push(header);
                    }
                    Ok(())
                },
            )
            .unwrap();
        headers
    };
    assert_eq!(
        sealed
            .iter()
            .map(|h| (h.turn, h.redacted, h.records, h.bytes))
            .collect::<Vec<_>>(),
        [
            (1, false, 16, 812_400),
            (2, true, 0, 0),
            (3, false, 16, 812_400),
            (4, false, 2, 9)
        ]
    );
    let codec = [
        records_of(&example()).unwrap(),
        Vec::new(),
        records_of(&example()).unwrap(),
        records_of(&small()).unwrap(),
    ];
    for read in views() {
        let mut want = Vec::new();
        for (header, records) in sealed.iter().zip(&codec) {
            want.push(ReadItem::Header(header.clone()));
            want.extend(expected(header.turn, records, &read));
        }
        let got = joined(
            pages(
                &store,
                &RecordRead {
                    page_bytes: 64 << 20,
                    ..read.clone()
                },
            )
            .unwrap(),
        );
        assert_eq!(got, want, "{:?} {:?}", read.kinds, read.heads);
    }
}

#[test]
fn a_head_states_the_full_size_and_a_piece_fetch_returns_the_whole() {
    let (_home, store) = mixed("records-read-heads");
    let result = &example()[2];
    let whole = crate::conversation::message::text_of(result).into_owned();
    for turn in [1, 3] {
        let read = RecordRead {
            turn: Some(turn),
            kinds: Some(vec![Kind::ToolResult]),
            heads: vec![(Kind::ToolResult, 200)],
            ..RecordRead::default()
        };
        let items = joined(pages(&store, &read).unwrap());
        let ReadItem::Record {
            record,
            tail: Some(back),
            truncated: true,
            ..
        } = &items[1]
        else {
            panic!("a head")
        };
        assert_eq!((record.seq, record.size), (2, 812_345));
        assert!(whole.starts_with(&record.text) && whole.ends_with(back.as_str()));
        let fetch = RecordRead {
            turn: Some(turn),
            seq: Some(9),
            piece: true,
            page_bytes: 262_144,
            ..RecordRead::default()
        };
        let fetched = pages(&store, &fetch).unwrap();
        assert!(fetched.len() > 3, "turn {turn}: paged");
        let text: String = joined(fetched)
            .iter()
            .filter_map(|item| match item {
                ReadItem::Record { record, .. } => Some(record.text.clone()),
                ReadItem::Header(_) => None,
            })
            .collect();
        assert_eq!(text, whole, "turn {turn}: the whole result");
    }
}
