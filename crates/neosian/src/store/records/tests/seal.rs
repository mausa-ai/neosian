//! The seal stopped after each of its steps, as a killed process leaves it
//! (§40.54 item 4.3): before its marker nothing is visible and the draft
//! stays open; from the marker on, the next operation on the conversation
//! rolls it forward and the whole turn is there, under one number.

use serde_json::json;

use super::super::seal::CRASH_AFTER;
use super::{enabled, example, messages, part, parts, spelled, turns};
use crate::conversation::turn_records::{State, records_of};

fn stopped_at(step: u8, with_records: bool) {
    let (_home, store) = enabled(&format!("records-seal-{step}-{with_records}"));
    store
        .append_turn(
            "c-1",
            messages(&[json!({"role": "user", "content": "first"})]),
            None,
        )
        .unwrap();
    let records = records_of(&example()).unwrap();
    let mut chunks = parts(&records, 300_000);
    let last = if with_records {
        chunks.pop().unwrap()
    } else {
        Vec::new()
    };
    for chunk in chunks {
        part(&store, "c-1", "d", chunk, false).unwrap();
    }
    CRASH_AFTER.with(|crash| crash.set(step));
    let stopped = part(&store, "c-1", "d", last.clone(), true);
    CRASH_AFTER.with(|crash| crash.set(0));
    assert!(
        stopped
            .unwrap_err()
            .text()
            .contains(&format!("stopped after seal step {step}"))
    );
    let read = turns(&store, "c-1");
    if step == 1 {
        assert_eq!(read.len(), 1, "step {step}: no turn");
        let probe = part(&store, "c-1", "d", Vec::new(), false).unwrap();
        let kept = records.len() - last.len();
        let open = if kept == 0 { State::None } else { State::Open };
        assert_eq!(
            (probe.state, probe.next_seq),
            (open, kept as u64),
            "step {step}"
        );
        let sealed = part(&store, "c-1", "d", last, true).unwrap();
        assert_eq!(sealed.turn.unwrap().turn, 2);
    } else {
        assert_eq!(
            read.len(),
            2,
            "step {step}: the whole turn once rolled forward"
        );
        assert_eq!(spelled(&read[1].messages), spelled(&example()));
        let probe = part(&store, "c-1", "d", Vec::new(), false).unwrap();
        assert_eq!(
            (probe.state, probe.turn.unwrap().turn),
            (State::Sealed, 2),
            "step {step}"
        );
    }
    assert_eq!(store.last_turn_number("c-1").unwrap(), 2);
    let next = store
        .append_turn(
            "c-1",
            messages(&[json!({"role": "user", "content": "next"})]),
            None,
        )
        .unwrap();
    assert_eq!(next.turn, 3, "step {step}: numbering stays gapless");
    let drafts = store.conversation_dir("c-1").unwrap().join(".drafts");
    assert!(!drafts.join("sealing").exists());
    let state = drafts.join("d.state").exists();
    assert!(!state, "step {step}: a sealed draft keeps no state (M4)");
}

#[test]
fn a_seal_stopped_after_any_step_leaves_no_turn_or_the_whole_turn() {
    for step in 1..=5 {
        stopped_at(step, false);
        stopped_at(step, true);
    }
}

#[test]
fn every_turn_operation_rolls_a_marked_seal_forward_first() {
    type Check = fn(&crate::store::FileStore) -> u64;
    let checks: [(&str, Check); 3] = [
        ("last_turn_number", |store| {
            store.last_turn_number("c-1").unwrap()
        }),
        ("first_turn_stamp", |store| {
            store.first_turn_stamp("c-1").unwrap().map_or(0, |_| 1)
        }),
        ("append_turn", |store| {
            store
                .append_turn(
                    "c-1",
                    messages(&[json!({"role": "user", "content": "x"})]),
                    None,
                )
                .unwrap()
                .turn
                - 1
        }),
    ];
    for (name, check) in checks {
        let (_home, store) = enabled(&format!("records-settle-{name}"));
        let records = records_of(&messages(&[json!({"role": "user", "content": "only"})])).unwrap();
        CRASH_AFTER.with(|crash| crash.set(2));
        assert!(part(&store, "c-1", "d", records, true).is_err());
        CRASH_AFTER.with(|crash| crash.set(0));
        assert_eq!(check(&store), 1, "{name}");
        assert_eq!(turns(&store, "c-1")[0].turn, 1, "{name}");
    }
}
