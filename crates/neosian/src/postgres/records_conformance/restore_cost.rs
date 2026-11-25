//! The cost of publishing a staged restore follows its headers, projections
//! and trail entries. Even a read lock on the physical records is unnecessary.
use super::Database;
use crate::conversation::message;
use crate::conversation::turn_records::{Header, Restore, Restored, Staged, records_of};
use crate::conversation::{Projection, Redaction};
use crate::limits::Records;
use crate::store::time::Stamp;
use serde_json::json;
use std::sync::mpsc;
use std::time::{Duration, Instant};

const LIMITS: Records = Records {
    draft_seconds: 86_400,
    max_turn_bytes: 64 << 20,
    max_record_turn_bytes: 1 << 30,
};
const ID: &str = "cost";

fn part() -> Restore {
    Restore {
        restore: "import".into(),
        ..Restore::default()
    }
}

fn stage(db: &Database, bytes_per_turn: usize) -> usize {
    let stamp = Stamp::parse("2026-10-02T09:15:00.001234Z").unwrap();
    let msg =
        message::from_json(&json!({"role":"user","content":"x".repeat(bytes_per_turn)})).unwrap();
    let records = records_of(&[msg]).unwrap();
    for n in [1, 2, 257, 1024] {
        for (index, chunk) in records.chunks(8).enumerate() {
            let turns = if index == 0 {
                vec![Header::of(n, stamp, None, &records)]
            } else {
                Vec::new()
            };
            let request = Restore {
                turns,
                records: chunk.iter().cloned().map(|r| (n, r)).collect(),
                ..part()
            };
            db.store.restore_records(ID, request, &LIMITS).unwrap();
        }
    }
    let metadata = Restore {
        turns: vec![Header {
            redacted: true,
            ..Header::of(2048, stamp, None, &[])
        }],
        projections_from: Some(0),
        projections: vec![Projection {
            turn: 1024,
            kind: "epoch".into(),
            span: 1,
            text: "completed".into(),
        }],
        redactions_from: Some(0),
        redactions: vec![Redaction {
            conversation_id: ID.into(),
            turns: vec![2048],
            actor: None,
            created_at: stamp,
        }],
        ..part()
    };
    db.store.restore_records(ID, metadata, &LIMITS).unwrap();
    records.len() * 4
}

fn publish(db: &Database) -> (Restored, Duration) {
    // The store was opened and used for staging before acquiring the lock.
    // Reads, writes and scans of the body table would all block here.
    db.execute("BEGIN; LOCK TABLE {{schema}}.turn_records IN ACCESS EXCLUSIVE MODE");
    let store = &db.store;
    std::thread::scope(|scope| {
        let (sent, received) = mpsc::sync_channel(1);
        let worker = scope.spawn(move || {
            let start = Instant::now();
            let result = store.restore_records(
                ID,
                Restore {
                    commit: true,
                    ..part()
                },
                &LIMITS,
            );
            sent.send((result, start.elapsed())).unwrap();
        });
        let result = received.recv_timeout(Duration::from_secs(10));
        db.execute("ROLLBACK");
        worker.join().unwrap();
        let (restored, duration) =
            result.expect("publication must finish while record access is locked");
        (restored.unwrap(), duration)
    })
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn publishing_four_small_or_large_turns_never_reads_or_writes_their_records() {
    let mut expected = vec![
        "conversations INSERT",
        "projections INSERT",
        "record_drafts UPDATE",
        "turn_redactions INSERT",
    ];
    expected.extend(["record_staging DELETE"; 7]);
    expected.extend(["record_turns INSERT"; 4]);
    expected.extend(["turns INSERT"; 5]);
    expected.sort_unstable();

    for bytes in [1024, 16 << 20] {
        let db = Database::new();
        let count = stage(&db, bytes);
        let before = db.record_addresses();
        assert_eq!(before.len(), count);
        assert_eq!(db.store.last_turn_number(ID).unwrap(), 0);
        db.observe_changes();

        let (restored, duration) = publish(&db);
        assert_eq!(restored.state, Staged::Committed);
        assert_eq!(db.store.last_turn_number(ID).unwrap(), 2048);
        assert_eq!(db.changes(), expected);
        assert_eq!(db.record_addresses(), before);
        eprintln!(
            "published {} body bytes in {} records: {} row changes, {:.3} ms",
            bytes * 4,
            count,
            expected.len(),
            duration.as_secs_f64() * 1000.0
        );
    }
}
