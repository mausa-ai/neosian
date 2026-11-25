//! Restoring into a trail-only target keeps the earlier erasure evidence.
use std::fs;
use std::io::Write;
use std::path::Path;

use super::enabled;
use super::restore_source::{ID, request, send, source, stream};
use crate::conversation::Redaction;
use crate::conversation::rows::{REDACTIONS, render_redaction};
use crate::conversation::turn_records::Restore;
use crate::store::FileStore;

fn act(actor: &str) -> Redaction {
    let mut act = source(0).acts.remove(0);
    act.actor = Some(actor.into());
    act
}

fn seed(store: &FileStore, old: &Redaction, terminated: bool) -> std::path::PathBuf {
    store
        .restore_conversation(ID, &[], &[], std::slice::from_ref(old))
        .unwrap();
    let trail = store.conversation_file(ID, REDACTIONS).unwrap();
    if !terminated {
        let file = fs::OpenOptions::new().write(true).open(&trail).unwrap();
        file.set_len(file.metadata().unwrap().len() - 1).unwrap();
    }
    assert_eq!(
        store.turn_redactions(None, None, 10).unwrap(),
        std::slice::from_ref(old)
    );
    trail
}

fn stage(store: &FileStore) -> Vec<Redaction> {
    let mut unit = source(100);
    unit.acts = vec![act("cli:a"), act("cli:b")];
    send(store, request(&stream(&unit))).unwrap();
    unit.acts
}

fn commit() -> Restore {
    Restore {
        commit: true,
        ..request(&[])
    }
}

fn append(path: &Path, bytes: &[u8]) {
    fs::OpenOptions::new()
        .append(true)
        .open(path)
        .unwrap()
        .write_all(bytes)
        .unwrap();
}

#[test]
fn records_preserve_acts_added_after_staging_and_replay_the_original_unit() {
    let (_home, clean) = enabled("restore-trail-clean");
    stage(&clean);
    let expected = send(&clean, commit()).unwrap();
    for terminated in [true, false] {
        for stop in [0, 1, 2, 3, 4, 5, 6, 7, 8, 9] {
            let (home, store) = enabled("restore-trail-interrupted");
            let added = stage(&store);
            let old = act("cli:prior");
            let trail = seed(&store, &old, terminated);
            super::super::commit::STOP.with(|s| s.set(stop));
            let result = send(&store, commit());
            super::super::commit::STOP.with(|s| s.set(0));
            if stop == 0 {
                assert_eq!(result.unwrap().digest, expected.digest);
            } else {
                assert!(
                    result
                        .unwrap_err()
                        .text()
                        .contains("stopped after restore step")
                );
            }
            drop(store);
            let store = FileStore::open(home.path()).unwrap();
            let reply = send(&store, commit()).unwrap();
            assert_eq!(reply.digest, expected.digest);
            assert_eq!((reply.stored_bytes, reply.freed_bytes), (0, 0));
            assert_eq!(
                store.turn_redactions(None, None, 10).unwrap(),
                [added[1].clone(), added[0].clone(), old.clone()]
            );
            assert_eq!(
                fs::read_to_string(trail).unwrap(),
                [
                    render_redaction(&old),
                    render_redaction(&added[0]),
                    render_redaction(&added[1])
                ]
                .concat()
            );
        }
    }
}

#[test]
fn legacy_restore_preserves_a_trail_and_leaves_empty_restores_untouched() {
    for terminated in [true, false] {
        let (_home, store) = enabled("legacy-restore-trail");
        let old = act("cli:prior");
        let trail = seed(&store, &old, terminated);
        let before = fs::read(&trail).unwrap();
        store.restore_conversation(ID, &[], &[], &[]).unwrap();
        assert_eq!(fs::read(&trail).unwrap(), before);
        let added = [act("cli:a"), act("cli:b")];
        store.restore_conversation(ID, &[], &[], &added).unwrap();
        assert_eq!(
            store.turn_redactions(None, None, 10).unwrap(),
            [added[1].clone(), added[0].clone(), old]
        );
    }
}

#[test]
fn unexpected_live_bytes_during_recovery_are_preserved() {
    let (home, store) = enabled("restore-trail-changed");
    stage(&store);
    let trail = seed(&store, &act("cli:prior"), true);
    super::super::commit::STOP.with(|s| s.set(8));
    let result = send(&store, commit());
    super::super::commit::STOP.with(|s| s.set(0));
    assert!(
        result
            .unwrap_err()
            .text()
            .contains("stopped after restore step")
    );
    append(&trail, render_redaction(&act("cli:other")).as_bytes());
    let bytes = fs::read(&trail).unwrap();
    drop(store);
    let store = FileStore::open(home.path()).unwrap();
    assert!(send(&store, commit()).is_err());
    assert_eq!(fs::read(trail).unwrap(), bytes);
}

#[test]
fn recovery_of_an_older_receipt_only_intent_uses_the_two_original_files() {
    let (home, store) = enabled("upgrade-restore-trail");
    let old = act("cli:old");
    let trail = seed(&store, &old, false);
    let added = stage(&store);
    super::super::commit::STOP.with(|s| s.set(1));
    let result = send(&store, commit());
    super::super::commit::STOP.with(|s| s.set(0));
    assert!(
        result
            .unwrap_err()
            .text()
            .contains("stopped after restore step")
    );
    let dir = store.staging(ID, &commit().restore).unwrap().dir;
    // The previous binary wrote the same .committing receipt and staged
    // incoming acts, with no prepared file. Its trail move is still pending.
    fs::remove_file(dir.join(format!("trail-{}", fs::metadata(&trail).unwrap().len()))).unwrap();
    drop(store);
    let store = FileStore::open(home.path()).unwrap();
    let expected = [added[1].clone(), added[0].clone(), old];
    assert_eq!(store.turn_redactions(None, None, 10).unwrap(), expected);
    assert_eq!(send(&store, commit()).unwrap().stored_bytes, 0);
    assert_eq!(store.turn_redactions(None, None, 10).unwrap(), expected);
}

#[test]
fn staged_suffixes_rollback_and_late_legacy_generations_stay_bounded() {
    use super::restore_source::Sent;
    let (_home, store) = enabled("restore-trail-generations");
    let old = act("cli:old");
    let trail = seed(&store, &old, false);
    let mut unit = source(100);
    let a = act("cli:a");
    let b = act("cli:b");
    unit.acts = vec![a.clone()];
    let staged_a = request(&stream(&unit));
    send(&store, staged_a.clone()).unwrap();
    let staging = store.staging(ID, &commit().restore).unwrap();
    let path = || {
        staging
            .dir
            .join(format!("trail-{}", fs::metadata(&trail).unwrap().len()))
    };
    // The bytes of a killed part reached both files before its state.
    append(&staging.redactions, b"unacknowledged");
    append(&path(), b"unacknowledged");
    send(&store, request(&[Sent::Act(1, b.clone())])).unwrap();
    send(&store, staged_a).unwrap();
    let mut expected = vec![old];
    for n in 0..5 {
        let late = act(&format!("cli:late-{n}"));
        store
            .restore_conversation(ID, &[], &[], std::slice::from_ref(&late))
            .unwrap();
        expected.push(late);
        assert!(
            fs::read_dir(&staging.dir)
                .unwrap()
                .filter_map(Result::ok)
                .filter(|e| e.file_name().to_string_lossy().starts_with("trail-"))
                .count()
                <= 2
        );
    }
    #[cfg(unix)]
    let prepared_inode = std::os::unix::fs::MetadataExt::ino(&fs::metadata(path()).unwrap());
    send(&store, commit()).unwrap();
    #[cfg(unix)]
    assert_eq!(
        std::os::unix::fs::MetadataExt::ino(&fs::metadata(&trail).unwrap()),
        prepared_inode
    );
    expected.extend([a, b]);
    expected.reverse();
    assert_eq!(store.turn_redactions(None, None, 10).unwrap(), expected);
}

#[test]
fn preparing_and_publishing_keep_heap_use_independent_of_existing_trail_bytes() {
    use super::memory::asked;
    for count in [1024, 32768] {
        let (_home, store) = enabled("restore-trail-memory");
        let old = act("cli:prior");
        let trail = seed(&store, &old, true);
        let row = render_redaction(&old);
        fs::write(trail, row.repeat(count)).unwrap();
        let part = request(&stream(&source(100)));
        let (reply, stage_bytes) = asked(|| send(&store, part));
        reply.unwrap();
        let (reply, commit_bytes) = asked(|| send(&store, commit()));
        reply.unwrap();
        assert!(
            stage_bytes < 1 << 20 && commit_bytes < 1 << 20,
            "{} retained bytes, {stage_bytes} allocated during staging and {commit_bytes} during commit",
            row.len() * count
        );
    }
}
