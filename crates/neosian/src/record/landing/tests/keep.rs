//! The keep limit (DESIGN §40.54 items 17.7 and 23; ruling D12 of the T1e1
//! plan): the setting's range; closed turns deleted in the order they
//! closed across conversations, held ones with pending ones; a conversation
//! another process holds skipped; the open span alone over the limit
//! deleted with the rest of its turn up to the next prompt; every deletion
//! counted; 512 MiB without the setting.

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::json;

use super::super::drops::{self, Drops};
use super::super::keep::{Arrival, admit, append, limit};
use super::super::queue::Queue;
use super::super::slots;
use super::{record, settings, spool, state};
use crate::record::spool::{Spool, line};
use crate::store::time::Stamp;
use crate::testing::Scratch;

const MIB: usize = 1 << 20;
const KEEP: &str = "[record]\nkeep_bytes = 16777216\n";
const NOW: &str = "2026-11-27T10:00:00.000000Z";

fn now() -> Stamp {
    Stamp::parse(NOW).unwrap()
}

/// A closed turn of `bytes` under `base`'s spool, in `queue`, closed at
/// second `second` of the day.
fn plant(base: &Path, queue: &str, id: &str, n: u64, bytes: usize, second: u32) -> PathBuf {
    let directory = spool(base).join(queue).join(id);
    fs::create_dir_all(&directory).unwrap();
    fs::write(Queue::part(&directory, n, "jsonl"), vec![b'x'; bytes]).unwrap();
    let mut closed = state("{}", bytes as u64);
    closed.closed_at = format!("2026-11-27T09:00:{second:02}.000000Z");
    closed.write(&Queue::part(&directory, n, "state")).unwrap();
    if queue == "held" {
        fs::write(
            Queue::part(&directory, n, "reason"),
            "{\"reason\": \"refused\"}",
        )
        .unwrap();
    }
    Queue::part(&directory, n, "jsonl")
}

fn arrive(base: &Path, id: &str, prompt: bool, bytes: u64) -> bool {
    let tree = spool(base);
    let arrival = Arrival {
        home: base,
        tree: &tree,
        id,
        prompt,
        bytes,
    };
    admit(&arrival, now()).unwrap()
}

fn gone(turn: &Path) -> bool {
    !turn.exists()
        && !turn.with_extension("state").exists()
        && !turn.with_extension("reason").exists()
}

fn drops(base: &Path) -> Option<Drops> {
    drops::read(&slots::directory(base))
}

fn counted(turns: u64, bytes: usize) -> Option<Drops> {
    Some(Drops {
        turns,
        bytes: bytes as u64,
        first: Some(NOW.into()),
        last: Some(NOW.into()),
    })
}

#[test]
fn a_value_outside_the_range_is_refused_and_none_means_512_mib() {
    let scratch = Scratch::new("keep-range");
    let home = scratch.path();
    assert_eq!(limit(home).unwrap(), 536_870_912);
    let refusal = "[record] keep_bytes must be an integer in 16777216..68719476736";
    for (text, expected) in [
        ("[record]\n", Ok(536_870_912)),
        ("[update]\nmode = 'auto'\n", Ok(536_870_912)),
        ("[broken", Ok(536_870_912)),
        ("[record]\nkeep_bytes = 16777216\n", Ok(16_777_216)),
        ("[record]\nkeep_bytes = 68719476736\n", Ok(68_719_476_736)),
        ("[record]\nkeep_bytes = 16777215\n", Err(refusal)),
        ("[record]\nkeep_bytes = 68719476737\n", Err(refusal)),
        ("[record]\nkeep_bytes = -1\n", Err(refusal)),
        ("[record]\nkeep_bytes = '512 MiB'\n", Err(refusal)),
        ("[record]\nkeep_bytes = 16777216.0\n", Err(refusal)),
    ] {
        scratch.plant("config.toml", text);
        let read = limit(home).map_err(|error| error.to_string());
        assert_eq!(read, expected.map_err(str::to_string), "{text}");
    }
    // The hook event that reads it is refused before its record is written.
    let tree = spool(home);
    let arrival = Arrival {
        home,
        tree: &tree,
        id: "a",
        prompt: true,
        bytes: 10,
    };
    assert_eq!(admit(&arrival, now()).unwrap_err().to_string(), refusal);
}

#[test]
fn without_the_setting_the_limit_is_512_mib() {
    let scratch = Scratch::new("keep-unset");
    let base = scratch.path();
    // Turns of 200 MiB, their files sparse: the limit counts their lengths.
    let turns: Vec<PathBuf> = (1..=3)
        .map(|n| plant(base, "pending", "a", n, 10, n as u32))
        .collect();
    let grow = |turn: &PathBuf| {
        let file = fs::OpenOptions::new().write(true).open(turn).unwrap();
        file.set_len(200 * MIB as u64).unwrap();
    };
    // 400 MiB and the record are under 512 MiB: nothing goes.
    turns[..2].iter().for_each(grow);
    assert!(arrive(base, "b", false, MIB as u64));
    assert!(turns.iter().all(|turn| turn.exists()));
    assert_eq!(drops(base), None);
    // 600 MiB pass it: the oldest goes, and is counted.
    grow(&turns[2]);
    assert!(arrive(base, "b", false, MIB as u64));
    assert!(gone(&turns[0]) && turns[1].exists() && turns[2].exists());
    assert_eq!(drops(base), counted(1, 200 * MIB));
}

#[test]
fn the_oldest_closed_turns_go_first_across_conversations() {
    let scratch = Scratch::new("keep-order");
    let base = scratch.path();
    scratch.plant("config.toml", KEEP);
    let a1 = plant(base, "pending", "a", 1, 6 * MIB, 2);
    let b1 = plant(base, "pending", "b", 1, 6 * MIB, 1);
    let a2 = plant(base, "pending", "a", 2, 6 * MIB, 3);
    // 18 MiB with the record: the oldest, b's, goes and the rest fits.
    assert!(arrive(base, "c", true, 1000));
    assert!(gone(&b1) && a1.exists() && a2.exists());
    assert_eq!(drops(base), counted(1, 6 * MIB));
    // An open span of 5 MiB pushes the total past again: a's first goes.
    fs::write(spool(base).join("c.jsonl"), vec![b'x'; 5 * MIB]).unwrap();
    assert!(arrive(base, "c", false, 1000));
    assert!(gone(&a1) && a2.exists());
    assert_eq!(drops(base), counted(2, 12 * MIB));
    // Below the limit nothing more goes.
    assert!(arrive(base, "c", false, 1000));
    assert!(a2.exists());
    assert_eq!(drops(base), counted(2, 12 * MIB));
}

#[test]
fn held_turns_count_with_pending_ones_and_go_by_the_same_order() {
    let scratch = Scratch::new("keep-held");
    let base = scratch.path();
    scratch.plant("config.toml", KEEP);
    let pending = plant(base, "pending", "a", 2, 6 * MIB, 2);
    let held = plant(base, "held", "a", 1, 6 * MIB, 1);
    fs::write(spool(base).join("b.jsonl"), vec![b'x'; 5 * MIB]).unwrap();
    // 17 MiB only when the held turn counts; it closed first, so it goes.
    assert!(arrive(base, "b", false, 10));
    assert!(gone(&held) && pending.exists());
    assert_eq!(drops(base), counted(1, 6 * MIB));
    // Its conversation's pending directory and lock stay as they were.
    assert!(spool(base).join("pending/a/2.state").is_file());
}

#[test]
fn a_conversation_whose_lock_is_held_is_skipped() {
    let scratch = Scratch::new("keep-locked");
    let base = scratch.path();
    scratch.plant("config.toml", KEEP);
    let a1 = plant(base, "pending", "a", 1, 6 * MIB, 1);
    let a2 = plant(base, "held", "a", 2, 6 * MIB, 2);
    let b1 = plant(base, "pending", "b", 1, 6 * MIB, 3);
    let queue = Queue::new(&spool(base));
    let lander = queue.try_lock("a").unwrap().expect("the lock is free");
    assert!(arrive(base, "c", true, 10));
    assert!(a1.exists() && a2.exists() && gone(&b1));
    assert_eq!(drops(base), counted(1, 6 * MIB));
    // When nothing else can go the record is still let in, and nothing
    // more is counted.
    assert!(arrive(base, "c", true, 5 * MIB as u64));
    assert!(a1.exists() && a2.exists());
    assert_eq!(drops(base), counted(1, 6 * MIB));
    drop(lander);
    assert!(arrive(base, "c", true, 5 * MIB as u64));
    assert!(gone(&a1) && a2.exists());
    assert_eq!(drops(base), counted(2, 12 * MIB));
}

#[test]
fn the_open_span_alone_over_the_limit_goes_with_its_turn_up_to_the_next_prompt() {
    let scratch = Scratch::new("keep-span");
    let base = scratch.path();
    scratch.plant("config.toml", KEEP);
    let older = plant(base, "pending", "a", 1, MIB, 1);
    let settings = settings(base, None);
    let spool_of = Spool::new(&settings.spool);
    let span = settings.spool.join("a.jsonl");
    fs::write(&span, vec![b'x'; 16 * MIB - 10]).unwrap();
    let tool = record(json!({"kind": "tool", "name": "t", "response": "r"}));
    let tool_bytes = line(&tool).len();
    // The span and this record pass 16 MiB by themselves: both go, as one
    // turn; the older closed turn stays, since deleting it would not help.
    assert!(!append(&settings, &spool_of, "a", &tool).unwrap());
    assert!(!span.exists() && older.exists());
    let first = 16 * MIB - 10 + tool_bytes;
    let read = drops(base).unwrap();
    assert_eq!((read.turns, read.bytes), (1, first as u64));
    // The rest of that turn is dropped and its bytes counted.
    assert!(!append(&settings, &spool_of, "a", &tool).unwrap());
    assert!(!span.exists());
    let read = drops(base).unwrap();
    assert_eq!((read.turns, read.bytes), (1, (first + tool_bytes) as u64));
    // The next prompt starts a new span.
    let prompt = record(json!({"kind": "prompt", "text": "again"}));
    assert!(append(&settings, &spool_of, "a", &prompt).unwrap());
    assert_eq!(fs::read_to_string(&span).unwrap(), line(&prompt));
    assert!(append(&settings, &spool_of, "a", &tool).unwrap());
    assert!(!settings.spool.join("a.dropped").exists());
    // A prompt arriving at a span over the limit starts the new span itself.
    fs::write(&span, vec![b'x'; 16 * MIB]).unwrap();
    assert!(append(&settings, &spool_of, "a", &prompt).unwrap());
    assert_eq!(fs::read_to_string(&span).unwrap(), line(&prompt));
    let read = drops(base).unwrap();
    let all = first + tool_bytes + 16 * MIB;
    assert_eq!((read.turns, read.bytes), (2, all as u64));
    assert!(older.exists());
}

#[test]
fn a_turn_put_back_goes_by_when_it_closed_not_by_its_number() {
    for queue in ["pending", "held"] {
        let scratch = Scratch::new(&format!("keep-requeued-{queue}"));
        let base = scratch.path();
        scratch.plant("config.toml", KEEP);
        // a's turn 3 was put back by `record held --retry`: a later number,
        // its first close time and its reason beside it, and maybe held
        // again. b's turn closed between a's two.
        let later = plant(base, "pending", "a", 2, 6 * MIB, 30);
        let requeued = plant(base, queue, "a", 3, 6 * MIB, 10);
        fs::write(
            requeued.with_extension("reason"),
            "{\"reason\": \"refused\"}",
        )
        .unwrap();
        let between = plant(base, "pending", "b", 1, 6 * MIB, 20);
        assert!(arrive(base, "c", true, 1000));
        let order = gone(&requeued) && later.exists() && between.exists();
        assert!(order, "{queue}");
        assert_eq!(drops(base), counted(1, 6 * MIB));
    }
}
