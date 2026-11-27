//! The login import's snapshot and units (the L1b slice of the L1 plan):
//! the home is never changed, credentials never enter the snapshot,
//! recovery runs on the copy alone, a used home's lock is held without
//! being written, a second take is refused, disposal leaves nothing, and a
//! conversation's unit is item 12's example: its digest and the size rule's
//! total of the wire's own answers (4,002 and 2,077 bytes).

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;
use std::thread;
use std::time::{Duration, Instant};

use serde_json::json;

use super::snapshot::{self, DIRECTORY, Fault};
use super::units::{Inventory, NO_TURNS, inventory};
use crate::conversation::message::{self, Message};
use crate::conversation::{Projection, Redaction, Turn};
use crate::store::FileStore;
use crate::store::time::Stamp;
use crate::testing::Scratch;

const DOC: &str = "---\nneosian_format: 1\nversion: 1\ncreated_at: '2026-10-01T00:00:00Z'\nupdated_at: '2026-10-01T00:00:00Z'\nactor: seed\n---\nkept\n";
const ROW: &str = "{\"neosian_format\":1,\"path\":\"a\",\"version\":1,\"action\":\"created\",\"content\":\"kept\\n\",\"actor\":\"seed\",\"created_at\":\"2026-10-01T00:00:00Z\",\"redacted\":false}\n";
const TOKEN: &str = "nsk-machine-credential-0123456789abcdef";
const ID: &str = "cc-7f3a";
const DIGEST: &str = "6ef0ac035d795df5f9b79c7d7a720b8d40aa4cc5eb90c3a4a3b0d717ddb53b32";

/// A home with one memory scope, a login credential, a configuration and
/// a queued turn, written as files: no FileStore has opened it.
fn home(name: &str) -> Scratch {
    let home = Scratch::new(name);
    home.plant("user%3Ame/documents/a.md", DOC);
    home.plant("user%3Ame/versions/a.jsonl", ROW);
    home.plant("login/credential.toml", &format!("token = \"{TOKEN}\"\n"));
    home.plant("config.toml", &format!("[store]\ntoken = \"{TOKEN}\"\n"));
    home.plant(
        "spool/pending/0001.json",
        &format!("{{\"bearer\":\"{TOKEN}\"}}"),
    );
    home
}

/// Every file of the home outside the snapshot's area, with its bytes.
fn tree(root: &Path) -> BTreeMap<String, Vec<u8>> {
    fn walk(dir: &Path, prefix: &str, found: &mut BTreeMap<String, Vec<u8>>) {
        for entry in fs::read_dir(dir).unwrap() {
            let entry = entry.unwrap();
            let name = format!("{prefix}{}", entry.file_name().to_string_lossy());
            if name == DIRECTORY {
                continue;
            }
            if entry.file_type().unwrap().is_dir() {
                walk(&entry.path(), &format!("{name}/"), found);
            } else {
                found.insert(name, fs::read(entry.path()).unwrap());
            }
        }
    }
    let mut found = BTreeMap::new();
    walk(root, "", &mut found);
    found
}

fn measured(home: &Path) -> (Inventory, Vec<Vec<u8>>) {
    let opened = snapshot::open(home).unwrap().expect("a sealed snapshot");
    let mut bodies = Vec::new();
    let made = inventory(&opened, &mut |_, _, body| {
        bodies.push(body.to_vec());
        Ok(())
    })
    .unwrap();
    (made, bodies)
}

#[cfg(unix)]
fn mode(path: &Path) -> u32 {
    use std::os::unix::fs::PermissionsExt;
    fs::metadata(path).unwrap().permissions().mode() & 0o777
}

#[test]
fn a_snapshot_takes_the_store_data_alone_and_leaves_the_home_as_it_was() {
    let home = home("snapshot-alone");
    let before = tree(home.path());
    let taken = snapshot::take(home.path()).unwrap();
    assert_eq!(tree(home.path()), before);
    assert!(!home.path().join(".store.lock").exists());
    let mut files = 0;
    let mut check = vec![home.path().join(DIRECTORY)];
    while let Some(dir) = check.pop() {
        #[cfg(unix)]
        assert_eq!(mode(&dir), 0o700, "{}", dir.display());
        for entry in fs::read_dir(&dir).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                check.push(path);
                continue;
            }
            files += 1;
            let text = fs::read(&path).unwrap();
            assert!(!String::from_utf8_lossy(&text).contains(TOKEN));
            #[cfg(unix)]
            assert_eq!(mode(&path), 0o600, "{}", path.display());
        }
    }
    // The two store files, the copy's own lock and the manifest.
    assert_eq!((taken.files, files), (2, 4));
    for kept_out in ["login", "config.toml", "spool"] {
        assert!(!taken.root().join(kept_out).exists(), "{kept_out}");
    }
    let (made, bodies) = measured(home.path());
    assert_eq!(made.units.len(), 1);
    let unit = &made.units[0];
    assert_eq!((unit.kind, unit.name.as_str()), ("scope", "user:me"));
    let counted = (unit.documents, unit.versions, unit.bytes);
    assert_eq!(counted, (1, 1, bodies[0].len() as u64));
    assert_eq!(tree(home.path()), before);
}

#[test]
fn a_pending_redo_plan_is_replayed_in_the_copy_alone() {
    let home = home("snapshot-recovery");
    let recovered = DOC.replace("kept", "recovered by the copy");
    let plan = json!({"format": 1, "writes": [
        {"path": "user%3Ame/documents/a.md", "operation": "put", "text": recovered},
    ]});
    home.plant(".transactions/pending.json", &plan.to_string());
    let before = tree(home.path());
    let taken = snapshot::take(home.path()).unwrap();
    assert_eq!(tree(home.path()), before);
    assert!(!taken.root().join(".transactions/pending.json").exists());
    let (_, bodies) = measured(home.path());
    let body = String::from_utf8(bodies[0].clone()).unwrap();
    assert!(body.contains("recovered by the copy"), "{body}");
}

#[test]
fn the_lock_of_a_used_home_is_held_without_being_written() {
    let home = home("snapshot-locked");
    home.plant(".store.lock", "\0");
    let before = tree(home.path());
    let held = crate::store::lock::acquire(&home.path().join(".store.lock")).unwrap();
    let writer = thread::spawn(move || {
        thread::sleep(Duration::from_millis(300));
        drop(held);
    });
    let started = Instant::now();
    snapshot::take(home.path()).unwrap();
    assert!(started.elapsed() >= Duration::from_millis(250));
    writer.join().unwrap();
    assert_eq!(tree(home.path()), before);
}

#[test]
fn a_second_take_is_refused_and_disposal_leaves_nothing() {
    let home = home("snapshot-dispose");
    let before = tree(home.path());
    let first = snapshot::take(home.path()).unwrap();
    match snapshot::take(home.path()) {
        Err(Fault::Exists { id }) => assert_eq!(id, first.id),
        other => panic!("{other:?}"),
    }
    assert_eq!(snapshot::open(home.path()).unwrap().unwrap().id, first.id);
    assert!(snapshot::dispose(home.path()).unwrap());
    assert!(!home.path().join(DIRECTORY).exists());
    assert!(snapshot::open(home.path()).unwrap().is_none());
    assert!(!snapshot::dispose(home.path()).unwrap());
    assert_eq!(tree(home.path()), before);
    // The same home yields the same content and so the same id.
    assert_eq!(snapshot::take(home.path()).unwrap().id, first.id);
}

#[test]
fn a_changed_snapshot_is_refused_when_it_is_opened() {
    let home = home("snapshot-changed");
    let taken = snapshot::take(home.path()).unwrap();
    let file = taken.root().join("user%3Ame/versions/a.jsonl");
    fs::write(&file, ROW.replace("kept", "edited")).unwrap();
    match snapshot::open(home.path()) {
        Err(Fault::Changed { path }) => assert_eq!(path, "user%3Ame/versions/a.jsonl"),
        other => panic!("{other:?}"),
    }
}

#[cfg(unix)]
#[test]
fn a_link_in_the_store_data_is_refused_by_name_and_nothing_stays() {
    let home = home("snapshot-link");
    let outside = Scratch::new("snapshot-link-outside");
    outside.plant("turns.jsonl", "");
    fs::create_dir_all(home.path().join("conversations")).unwrap();
    let link = home.path().join("conversations/cc-x");
    std::os::unix::fs::symlink(outside.path(), link).unwrap();
    match snapshot::take(home.path()) {
        Err(Fault::Unsupported { path, what }) => {
            assert_eq!(
                (path.as_str(), what),
                ("conversations/cc-x", "a symbolic link")
            );
        }
        other => panic!("{other:?}"),
    }
    assert!(!home.path().join(DIRECTORY).exists());
}

fn said(role: &str, text: &str) -> Message {
    message::from_json(&json!({"role": role, "content": text})).unwrap()
}

fn stamp(text: &str) -> Stamp {
    Stamp::parse(text).unwrap()
}

/// Item 12's example (`assets/docs/wire.md`), beside a conversation with a
/// projection alone and one with an erasure trail alone.
fn conversations(home: &Path) {
    let store = FileStore::open(home).unwrap();
    let turn = |turn, time: &str, messages: Vec<Message>| Turn {
        conversation_id: ID.into(),
        turn,
        redacted: messages.is_empty(),
        messages,
        created_at: stamp(time),
        actor: Some("claude-code:7f3a".into()),
    };
    let first = vec![
        said("system", &"x".repeat(32)),
        said("user", &"y".repeat(500)),
        said("assistant", &"z".repeat(1000)),
    ];
    let turns = [
        turn(1, "2026-10-02T09:15:00.000001Z", first),
        turn(2, "2026-10-02T09:20:11Z", Vec::new()),
        turn(
            3,
            "2026-10-02T09:31:40Z",
            vec![said("user", &"w".repeat(44))],
        ),
    ];
    let entry = |text: &str| Projection {
        turn: 3,
        kind: "epoch".into(),
        text: text.into(),
        span: 3,
    };
    let act = |id: &str| Redaction {
        conversation_id: id.into(),
        turns: vec![2],
        actor: Some("client:default/ops".into()),
        created_at: stamp("2026-10-05T08:00:00Z"),
    };
    let example = [entry("set up the cache")];
    store
        .restore_conversation(ID, &turns, &example, &[act(ID)])
        .unwrap();
    let kept = [entry("kept")];
    store
        .restore_conversation("cc-entries", &[], &kept, &[])
        .unwrap();
    let trail = [act("cc-trail")];
    store
        .restore_conversation("cc-trail", &[], &[], &trail)
        .unwrap();
}

#[test]
fn a_conversation_is_measured_by_its_unit_digest_and_the_size_rule() {
    let home = Scratch::new("snapshot-example");
    conversations(home.path());
    let before = tree(home.path());
    snapshot::take(home.path()).unwrap();
    assert_eq!(tree(home.path()), before);
    let (made, bodies) = measured(home.path());
    assert_eq!(made.units.len(), 1);
    let unit = &made.units[0];
    assert_eq!((unit.name.as_str(), unit.sha256.as_str()), (ID, DIGEST));
    // The wire's example answers 4,002 and 2,077 stored bytes.
    assert_eq!((unit.bytes, unit.parts), (4_002 + 2_077, 1));
    assert_eq!((unit.turns, unit.projections, unit.redactions), (3, 1, 1));
    let omitted: Vec<_> = made
        .omitted
        .iter()
        .map(|o| (o.name.as_str(), o.reason, o.projections, o.redactions))
        .collect();
    let expected = [("cc-entries", NO_TURNS, 1, 0), ("cc-trail", NO_TURNS, 0, 1)];
    assert_eq!(omitted, expected);
    let body: serde_json::Value = serde_json::from_slice(&bodies[0]).unwrap();
    assert_eq!(body["commit"], json!(true));
    assert_eq!(body["redactions"][0]["turns"], json!([2]));
    assert_eq!(made.totals(), (0, 1, 6_079));
    // A second measure of the same snapshot answers the same bytes.
    assert_eq!(measured(home.path()), (made, bodies));
}
