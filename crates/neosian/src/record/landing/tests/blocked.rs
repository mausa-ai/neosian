//! A destination's record (DESIGN §40.54 items 17.4, 17.5 and 23) on an
//! injected clock: a wait and its failing time, a block and its six hours,
//! the claimed attempt, and what each resume ends. The record names its
//! destination by a key computed here by hand and holds no secret.

use std::time::{Duration, UNIX_EPOCH};

use super::super::destination::{
    Gate, Record, SIX_HOURS, claim, gate, key, landed, note, read, stamp, unblock, unclaim,
};
use super::super::gate::host;
use super::super::rules::{Block, Stop};
use super::super::state::{Destination, describe};
use crate::remote::selection::Selection;
use crate::store::time::Stamp;
use crate::testing::Scratch;

const DAY_ONE: u64 = 1_795_000_000;

fn at(seconds: i64) -> Stamp {
    stamp(UNIX_EPOCH + Duration::from_secs(DAY_ONE)).after(seconds)
}

fn wait(seconds: u64, failing: bool) -> Stop {
    let message = "the destination answered 503: down".to_string();
    Stop::Wait {
        seconds,
        failing,
        message,
    }
}

fn block(block: Block, status: u16, code: &str) -> Stop {
    Stop::Block {
        block,
        message: format!("[{code}] no"),
        status: Some(status),
        code: Some(code.into()),
    }
}

fn file() -> Destination {
    Destination::File {
        root: "/store".into(),
    }
}

#[test]
fn the_key_is_the_descriptor_s_digest_computed_by_hand() {
    // SHA-256 of `{"kind":"file","root":"/store"}`, the first 32 digits.
    assert_eq!(key(&file()), "e318356f94f049204355564033938ab3");
}

#[test]
fn a_wait_closes_the_destination_until_its_time_and_a_landing_ends_its_faults() {
    let scratch = Scratch::new("record-wait");
    let (landing, key) = (scratch.path(), key(&file()));
    assert_eq!(gate(landing, &file(), at(0)), Gate::Open);
    note(landing, &key, "h", &wait(60, true), at(0)).unwrap();
    let first = read(landing, &key).unwrap();
    assert_eq!(
        first.wait_until.as_deref(),
        Some("2026-11-18T11:07:40.000000Z")
    );
    assert_eq!(
        first.failing_since.as_deref(),
        Some("2026-11-18T11:06:40.000000Z")
    );
    assert_eq!(gate(landing, &file(), at(59)), Gate::Closed);
    assert_eq!(gate(landing, &file(), at(60)), Gate::Open);
    // A later fault keeps when the faults began; a 429 never starts them.
    note(landing, &key, "h", &wait(60, true), at(600)).unwrap();
    let later = read(landing, &key).unwrap();
    assert_eq!(later.failing_since, first.failing_since);
    assert_ne!(later.wait_until, first.wait_until);
    landed(landing, &key).unwrap();
    assert_eq!(read(landing, &key), None, "nothing left, the record goes");
    note(landing, &key, "h", &wait(11, false), at(0)).unwrap();
    assert_eq!(read(landing, &key).unwrap().failing_since, None);
    // A server's Retry-After cannot outlast six hours, nor overflow a stamp.
    note(landing, &key, "h", &wait(u64::MAX, false), at(0)).unwrap();
    assert_eq!(gate(landing, &file(), at(SIX_HOURS - 1)), Gate::Closed);
    assert_eq!(gate(landing, &file(), at(SIX_HOURS)), Gate::Open);
}

#[test]
fn a_block_waits_six_hours_for_one_claimed_attempt_and_restarts_when_refused() {
    let scratch = Scratch::new("record-block");
    let (landing, key) = (scratch.path(), key(&file()));
    note(landing, &key, "h", &wait(60, true), at(0)).unwrap();
    note(
        landing,
        &key,
        "h",
        &block(Block::Storage, 413, "storage_exceeded"),
        at(10),
    )
    .unwrap();
    let blocked = read(landing, &key).unwrap();
    assert_eq!(blocked.wait_until, None, "a block ends a wait");
    let held = blocked.block.clone().unwrap();
    assert_eq!((held.reason.as_str(), held.status), ("storage", Some(413)));
    assert_eq!(held.code.as_deref(), Some("storage_exceeded"));
    assert_eq!(held.message, "[storage_exceeded] no");
    let six = SIX_HOURS + 10;
    assert_eq!(
        gate(landing, &file(), at(six - 60)),
        Gate::Closed,
        "5 h 59 min"
    );
    assert_eq!(gate(landing, &file(), at(six)), Gate::Due);
    // One lander claims the attempt; a second finds it closed.
    let was = claim(landing, &key, at(six)).unwrap().unwrap();
    assert_eq!(was, held);
    assert_eq!(claim(landing, &key, at(six)).unwrap(), None);
    assert_eq!(gate(landing, &file(), at(six)), Gate::Closed);
    // An attempt that sent nothing gives its claim back.
    unclaim(landing, &key, &was).unwrap();
    assert_eq!(gate(landing, &file(), at(six)), Gate::Due);
    // Refused again: the six hours start over, from the same block.
    claim(landing, &key, at(six)).unwrap().unwrap();
    note(
        landing,
        &key,
        "h",
        &block(Block::Storage, 413, "storage_exceeded"),
        at(six),
    )
    .unwrap();
    let again = read(landing, &key).unwrap().block.unwrap();
    assert_eq!(again.since, held.since);
    assert_eq!(gate(landing, &file(), at(2 * SIX_HOURS + 9)), Gate::Closed);
    assert_eq!(gate(landing, &file(), at(2 * SIX_HOURS + 10)), Gate::Due);
    // A wait during a block leaves the block; another reason starts anew.
    note(landing, &key, "h", &wait(60, true), at(six + 1)).unwrap();
    assert_eq!(read(landing, &key).unwrap().block.unwrap(), again);
    note(
        landing,
        &key,
        "h",
        &block(Block::Forbidden, 403, "forbidden"),
        at(six + 2),
    )
    .unwrap();
    assert_ne!(
        read(landing, &key).unwrap().block.unwrap().since,
        held.since
    );
}

#[test]
fn a_login_ends_credential_blocks_and_retry_ends_every_block_and_wait() {
    let scratch = Scratch::new("record-resume");
    let landing = scratch.path();
    let named = |root: &str| Destination::File { root: root.into() };
    let (storage, credential, waiting) = (named("/s"), named("/c"), named("/w"));
    let full = block(Block::Storage, 413, "storage_exceeded");
    note(landing, &key(&storage), "s", &full, at(0)).unwrap();
    let refused = block(Block::Credential, 401, "unauthorized");
    note(landing, &key(&credential), "c", &refused, at(0)).unwrap();
    note(landing, &key(&waiting), "w", &wait(60, false), at(0)).unwrap();
    let ended = unblock(landing, |block| block.reason == "credential", false).unwrap();
    assert_eq!(
        ended.iter().map(|r| r.host.as_str()).collect::<Vec<_>>(),
        ["c"]
    );
    assert_eq!(gate(landing, &credential, at(1)), Gate::Open);
    assert_eq!(gate(landing, &storage, at(1)), Gate::Closed);
    assert_eq!(gate(landing, &waiting, at(1)), Gate::Closed);
    let ended = unblock(landing, |_| true, true).unwrap();
    let mut hosts: Vec<_> = ended.iter().map(|r| r.host.clone()).collect();
    hosts.sort();
    assert_eq!(hosts, ["s", "w"]);
    assert_eq!(gate(landing, &storage, at(1)), Gate::Open);
    assert_eq!(gate(landing, &waiting, at(1)), Gate::Open);
    assert!(unblock(landing, |_| true, true).unwrap().is_empty());
}

#[test]
fn a_record_names_the_host_and_holds_no_secret() {
    let scratch = Scratch::new("record-secret");
    let url = "https://user:pa55word@cloud.example:8443/state?tenant=q1w2";
    let selection = Selection::Remote {
        url: url.into(),
        token: "t0ken-value".into(),
    };
    let destination = describe(&selection);
    let (landing, key) = (scratch.path(), key(&destination));
    let refused = block(Block::Credential, 401, "unauthorized");
    note(landing, &key, &host(&selection), &refused, at(0)).unwrap();
    let text = std::fs::read_to_string(landing.join(format!("{key}.json"))).unwrap();
    for secret in ["user", "pa55word", "t0ken-value", "tenant", "q1w2"] {
        assert!(!text.contains(secret), "{secret} in {text}");
    }
    assert_eq!(host(&selection), "cloud.example:8443");
    let dsn = Selection::Postgres {
        dsn: "postgresql://admin:hunter2@db.example:5432/app".into(),
        schema: "neosian".into(),
    };
    assert_eq!(host(&dsn), "db.example:5432 (schema neosian)");
}

#[test]
fn a_damaged_record_or_time_never_closes_a_destination_for_good() {
    let scratch = Scratch::new("record-damaged");
    let (landing, key) = (scratch.path(), key(&file()));
    std::fs::write(landing.join(format!("{key}.json")), "{not json").unwrap();
    assert_eq!(gate(landing, &file(), at(0)), Gate::Open);
    let record = Record {
        host: "h".into(),
        wait_until: Some("tomorrow".into()),
        ..Record::default()
    };
    assert_eq!(record.gate(at(0)), Gate::Open);
}

#[test]
fn ending_every_block_leaves_the_drop_counts_and_the_notices_as_they_were() {
    let scratch = Scratch::new("landing-unblock-files");
    let landing = scratch.path().join("landing");
    let (destination, full) = (file(), block(Block::Storage, 413, "storage_exceeded"));
    note(&landing, &key(&destination), "/store", &full, at(0)).unwrap();
    let kept = [
        (
            "drops.json",
            "{\"turns\":5,\"bytes\":9,\"first\":null,\"last\":null}\n",
        ),
        ("notices.ack", "{\"blocks\":[]}\n"),
        ("notes.json", "{}\n"),
        ("0123456789ABCDEF0123456789ABCDEF.json", "{}\n"),
    ];
    for (name, text) in kept {
        std::fs::write(landing.join(name), text).unwrap();
    }
    let ended = unblock(&landing, |_| true, true).unwrap();
    assert_eq!(ended.len(), 1, "the one record");
    assert!(read(&landing, &key(&destination)).is_none());
    for (name, text) in kept {
        assert_eq!(
            std::fs::read_to_string(landing.join(name)).unwrap(),
            text,
            "{name}"
        );
    }
}
