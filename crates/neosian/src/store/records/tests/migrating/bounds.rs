//! The two bounds a migration meets (§40.54 items 15.1, 16 and 21,
//! T1b2c3): a turn past `NEOSIAN_MAX_TURN_BYTES` is migrated all the same,
//! after which the legacy read refuses it by name, a search hands it on as
//! omitted and the record read answers it in parts; and the memory a
//! migration holds follows one turn, whatever the length of the log.

use serde_json::json;

use super::super::memory::peak;
use super::super::searching::{answer, request};
use super::super::{enabled, example};
use super::{log, migrate, moved, say};
use crate::conversation::rank::Order;
use crate::conversation::turn_records::{RecordRead, records_of};

#[test]
fn a_turn_past_the_legacy_bound_migrates_and_is_read_in_parts() {
    let (_home, store) = enabled("records-migrate-long");
    store.append_turn("c-1", example(), None).unwrap();
    assert_eq!(migrate(&store, "c-1"), moved("c-1", 1, 0, 0));
    let row = || store.last_row(&log(&store, "c-1"), "c-1").unwrap().unwrap();
    assert_eq!(
        store.whole("c-1", row(), 65_536).unwrap_err().text(),
        "resource limit NEOSIAN_MAX_TURN_BYTES exceeded: turn 1 of c-1 holds 812400 > 65536 bytes; a newer neosian reads this turn in parts"
    );
    assert_eq!(
        store.whole("c-1", row(), 812_400).unwrap().messages.len(),
        4
    );
    let mut records = 0;
    let read = RecordRead {
        turn: Some(1),
        ..RecordRead::default()
    };
    let mut visit = |_| {
        records += 1;
        Ok(())
    };
    assert!(
        store
            .read_records("c-1", &read, &mut visit)
            .unwrap()
            .is_none()
    );
    assert_eq!(records, 1 + records_of(&example()).unwrap().len());
    let low = answer(&store, &request("tests", Order::Newest), Some(65_536));
    assert!(low[0].0.starts_with("omitted c-1 1 "), "{:?}", low[0]);
    let raised = answer(&store, &request("tests", Order::Newest), Some(812_400));
    assert!(raised[0].0.contains("\"turn\":1,"), "{:.80}", raised[0].0);
}

/// The most a migration of `turns` turns of 200,000 bytes held at once.
fn held(turns: u64) -> usize {
    let (_home, store) = enabled(&format!("records-migrate-memory-{turns}"));
    let result = format!("{}\n", "r".repeat(99)).repeat(2_000);
    for _ in 0..turns {
        let turn = [
            json!({"role": "user", "content": "run it"}),
            json!({"role": "tool", "content": result, "tool_call_id": "t"}),
        ];
        say(&store, "c-1", &turn, None);
    }
    let (done, held) = peak(|| migrate(&store, "c-1"));
    assert_eq!(done, moved("c-1", turns, 0, 0));
    held
}

#[test]
fn memory_during_a_migration_follows_one_turn_not_the_log() {
    let (few, many) = (held(4), held(64));
    eprintln!("migration peaks: {few} bytes for 4 turns of 200,000 bytes, {many} for 64");
    assert!(
        many <= few + (64 << 10),
        "4 turns held {few}, 64 held {many}"
    );
    assert!(many < 2 << 20, "a 200,000-byte turn held {many} bytes");
}
