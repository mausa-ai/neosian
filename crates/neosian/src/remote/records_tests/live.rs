//! The round trip against real `neosian serve` processes (ruling R38):
//! a turn written in parts, read back in pages, searched, a conversation
//! restored in parts and committed, and a sweep preview. Ignored by
//! default. `NEOSIAN_LIVE_RECORDS_URLS` names the servers, separated by
//! commas, and `NEOSIAN_LIVE_RECORDS_TOKEN` their bearer token. Each run
//! writes two conversations with fresh ids and leaves them in the store.

use std::time::{SystemTime, UNIX_EPOCH};

use super::record;
use crate::conversation::message::Role;
use crate::conversation::rank::Order;
use crate::conversation::turn_records::{
    Header, Kind, RecordRead, Restore, Staged, State, TurnRecord, UnitDigest, Write, restore_id,
    split,
};
use crate::conversation::{Projection, Redaction};
use crate::remote::RemoteStore;
use crate::remote::records::{Answered, RecordQuery};
use crate::store::time::Stamp;

/// A prompt of three chunks and a reply that holds `needle`.
fn turn(needle: &str) -> Vec<TurnRecord> {
    let line = "the quick brown fox jumps over the lazy dog\n";
    let prompt = line.repeat(150_000 / line.len());
    let chunks = split(&prompt);
    assert_eq!(chunks.len(), 3);
    let mut records: Vec<TurnRecord> = Vec::new();
    for (at, chunk) in chunks.iter().enumerate() {
        let mut one = record(at as u64, 0, Role::User, Kind::Prompt, chunk);
        (one.chunk, one.size) = (at as u64, prompt.len() as u64);
        if at > 0 {
            one.content = None;
        }
        records.push(one);
    }
    let reply = format!("done, {needle} found");
    records.push(record(3, 1, Role::Assistant, Kind::Reply, &reply));
    records
}

/// Every page of `read`: the records, the headers and how many pages
/// came. The client's forward check runs on each page.
fn view(store: &RemoteStore, id: &str, read: RecordRead) -> (Vec<Answered>, Vec<Header>, usize) {
    let (mut records, mut headers, mut count) = (Vec::new(), Vec::new(), 0);
    let mut visit = |page: super::RecordPage| {
        count += 1;
        headers.extend(page.turns);
        records.extend(page.records);
        Ok(true)
    };
    store.read_records(id, &read, &mut visit).unwrap();
    (records, headers, count)
}

/// Every page of a whole read with pages of `page_bytes`.
fn pages(store: &RemoteStore, id: &str, page_bytes: u64) -> (Vec<Answered>, Vec<Header>, usize) {
    let read = RecordRead {
        page_bytes,
        ..RecordRead::default()
    };
    view(store, id, read)
}

fn round_trip(url: &str, token: &str) {
    let store = RemoteStore::connect(url, token).unwrap();
    assert!(store.supports_records(), "{url} declares no turn records");
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH).unwrap();
    let unique = format!("{:x}", nanos.as_nanos());
    let (id, needle) = (format!("t1d6-live-{unique}"), format!("needle{unique}"));
    let records = turn(&needle);
    let bytes: u64 = records.iter().map(|one| one.text.len() as u64).sum();
    println!(
        "{url}: conversation {id}, {bytes} bytes in {} records",
        records.len()
    );

    // A turn in two parts, with a probe between them.
    let part = |records: &[TurnRecord], seal| Write {
        draft: "d1".into(),
        records: records.to_vec(),
        turn_bytes: Some(bytes),
        seal,
        actor: Some("t1d6:live".into()),
        ..Write::default()
    };
    let opened = store
        .write_records(&id, &part(&records[..2], false))
        .unwrap();
    assert_eq!((opened.state, opened.next_seq), (State::Open, 2));
    let probed = store.write_records(&id, &part(&[], false)).unwrap();
    assert_eq!(
        (probed.state, probed.next_seq, probed.stored_bytes),
        (State::Open, 2, 0)
    );
    let sealed = store
        .write_records(&id, &part(&records[2..], true))
        .unwrap();
    assert_eq!((sealed.state, sealed.next_seq), (State::Sealed, 4));
    let header = sealed.turn.unwrap();
    assert_eq!((header.turn, header.records, header.bytes), (1, 4, bytes));
    println!(
        "  written: stored {} + {}, freed {}, actor {:?}",
        opened.stored_bytes, sealed.stored_bytes, sealed.freed_bytes, header.actor
    );
    // A sealed draft refuses new records with its header in the details.
    let late = record(4, 2, Role::User, Kind::Prompt, "late");
    let refused = store.write_records(&id, &part(&[late], false)).unwrap_err();
    assert_eq!(refused.reason(), Some("draft_sealed"), "{refused}");
    let refused = super::refused(refused);
    assert_eq!(
        (refused.status, &refused.details["turn"]),
        (400, &header.to_json())
    );

    // Read back in pages of at most 70,000 bytes: one chunk to a page.
    let (answered, headers, count) = pages(&store, &id, 70_000);
    assert!(count >= 3, "{count} pages");
    assert!(headers.iter().all(|one| *one == header));
    let read: Vec<TurnRecord> = answered.iter().map(|one| one.record.clone()).collect();
    assert_eq!(read, records);
    assert!(answered.iter().all(|one| one.turn == 1 && !one.truncated));
    println!("  read: {} records in {count} pages", read.len());
    // The views a server answers, each in small pages, so the forward
    // check meets a head, a view that leaves a turn's records out, a piece
    // read from its middle and headers alone.
    let small = RecordRead {
        page_bytes: 70_000,
        ..RecordRead::default()
    };
    let heads = RecordRead {
        heads: vec![(Kind::Prompt, 200)],
        ..small.clone()
    };
    let (answered, _, _) = view(&store, &id, heads);
    assert_eq!(answered.len(), 2);
    assert!(answered[0].truncated && answered[0].tail.is_some());
    let replies = RecordRead {
        kinds: Some(vec![Kind::Reply]),
        page_bytes: 1,
        ..RecordRead::default()
    };
    let (answered, headers, _) = view(&store, &id, replies);
    assert_eq!((answered.len(), answered[0].record.seq), (1, 3));
    assert!(headers.iter().all(|one| *one == header));
    let piece = RecordRead {
        turn: Some(1),
        seq: Some(1),
        piece: true,
        ..small.clone()
    };
    let (answered, _, count) = view(&store, &id, piece);
    let seqs: Vec<u64> = answered.iter().map(|one| one.record.seq).collect();
    assert_eq!((seqs, count >= 3), (vec![0, 1, 2], true));
    let none = RecordRead {
        kinds: Some(vec![]),
        ..small
    };
    let (answered, headers, _) = view(&store, &id, none);
    assert_eq!((answered.len(), headers), (0, vec![header.clone()]));
    println!(
        "  views: a head, replies alone in 1-byte pages, a piece in {count} pages, headers alone"
    );

    let query = RecordQuery {
        query: needle.clone(),
        conversations: Some(vec![id.clone()]),
        order: Some(Order::Relevance),
        ..RecordQuery::default()
    };
    let hits = store.search_records(&query).unwrap();
    assert_eq!(hits.len(), 1);
    assert_eq!(
        (hits[0].turn, hits[0].seq, hits[0].kind),
        (1, 3, Kind::Reply)
    );
    assert!(hits[0].excerpt.1.contains(&needle) && hits[0].score.is_some());
    println!("  searched: seq {} scored {:?}", hits[0].seq, hits[0].score);

    // The same turn and an erased one restored onto a second conversation.
    let target = format!("{id}-r");
    let at = |text| Stamp::parse(text).unwrap();
    let first = Header {
        turn: 1,
        created_at: at("2026-10-02T09:15:00.000001Z"),
        actor: Some("someone:else".into()),
        redacted: false,
        records: 4,
        bytes,
    };
    let erased = Header {
        turn: 2,
        created_at: at("2026-10-02T09:20:11Z"),
        actor: None,
        redacted: true,
        records: 0,
        bytes: 0,
    };
    let entry = Projection {
        turn: 1,
        kind: "log".into(),
        text: "wrote the fox".into(),
        span: 1,
    };
    let act = Redaction {
        conversation_id: target.clone(),
        turns: vec![2],
        actor: Some("client:default/ops".into()),
        created_at: at("2026-10-05T08:00:00Z"),
    };
    let mut digest = UnitDigest::default();
    digest.header(&first);
    records.iter().for_each(|one| digest.record(1, one));
    digest.header(&erased);
    digest.projection(&entry);
    digest.act(&act);
    let restore = |turns: Vec<Header>, records: &[TurnRecord]| Restore {
        restore: restore_id(&target),
        turns,
        records: records.iter().map(|one| (1, one.clone())).collect(),
        ..Restore::default()
    };
    let probe = restore(vec![], &[]);
    assert_eq!(
        store.restore_records(&target, &probe).unwrap().state,
        Staged::None
    );
    let opened = store
        .restore_records(&target, &restore(vec![first.clone()], &records[..2]))
        .unwrap();
    let position = opened.position.unwrap();
    assert_eq!(opened.state, Staged::Open);
    assert_eq!(
        (position.turn, position.seq, position.complete),
        (1, 2, false)
    );
    let probed = store.restore_records(&target, &probe).unwrap();
    assert_eq!(
        (probed.state, probed.position),
        (Staged::Open, Some(position))
    );
    assert!(
        pages(&store, &target, 70_000).1.is_empty(),
        "visible before the commit"
    );
    let last = Restore {
        projections_from: Some(0),
        projections: vec![entry],
        redactions_from: Some(0),
        redactions: vec![act],
        commit: true,
        ..restore(vec![erased.clone()], &records[2..])
    };
    let committed = store.restore_records(&target, &last).unwrap();
    assert_eq!(committed.state, Staged::Committed);
    assert_eq!(committed.digest, Some(digest.finish()));
    let position = committed.position.unwrap();
    assert_eq!(
        (position.turn, position.projections, position.redactions),
        (2, 1, 1)
    );
    let (answered, headers, count) = pages(&store, &target, 70_000);
    assert_eq!(headers.last(), Some(&erased));
    assert_eq!(headers[0], first);
    let read: Vec<TurnRecord> = answered.into_iter().map(|one| one.record).collect();
    assert_eq!(read, records);
    println!(
        "  restored: {target} digest {} read back in {count} pages",
        committed.digest.unwrap()
    );

    // A preview of the sweep, followed to its end.
    let (mut cursor, mut calls, mut stale) = (None::<String>, 0, 0);
    loop {
        let page = store.sweep_records(100, cursor.as_deref(), false).unwrap();
        assert!(!page.write);
        (calls, stale) = (calls + 1, stale + page.conversations.len());
        assert!(calls < 1_000, "the sweep does not end");
        match page.next_cursor {
            Some(next) => cursor = Some(next),
            None => break,
        }
    }
    println!("  swept (preview): {stale} conversations with stale items in {calls} calls");
}

#[test]
#[ignore = "requires NEOSIAN_LIVE_RECORDS_URLS and NEOSIAN_LIVE_RECORDS_TOKEN"]
fn records_round_trip_through_live_servers() {
    let urls = std::env::var("NEOSIAN_LIVE_RECORDS_URLS")
        .expect("set NEOSIAN_LIVE_RECORDS_URLS to the servers to test, separated by commas");
    let token = std::env::var("NEOSIAN_LIVE_RECORDS_TOKEN")
        .expect("set NEOSIAN_LIVE_RECORDS_TOKEN to their bearer token");
    for url in urls.split(',').filter(|url| !url.is_empty()) {
        round_trip(url, &token);
    }
}
