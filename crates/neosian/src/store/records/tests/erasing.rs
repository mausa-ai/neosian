//! Item 20. The count, projections and trail use the same selection for
//! both row formats. A whole-conversation erase also reaches pending state.
use super::restore_source::{ID, request, send, source, stream};
use super::{enabled, messages, part};
use crate::conversation::Projection;
use crate::conversation::turn_records::{Restore, Staged, records_of};
use serde_json::json;

#[test]
fn selective_erasure_skeletonizes_records_and_keeps_open_drafts() {
    let (_home, store) = enabled("records-erase-selective");
    let data = messages(&[json!({"role":"user","content":"archived text"})]);
    store
        .append_turn(
            ID,
            messages(&[json!({"role":"user","content":"legacy"})]),
            None,
        )
        .unwrap();
    let sealed = part(&store, ID, "d", records_of(&data).unwrap(), true)
        .unwrap()
        .turn
        .unwrap();
    part(&store, ID, "pending", records_of(&data).unwrap(), false).unwrap();
    let projection = Projection {
        turn: 2,
        span: 2,
        kind: "epoch".into(),
        text: "cover".into(),
    };
    store.append_projections(ID, &[projection]).unwrap();
    assert_eq!(
        store
            .redact_turns(ID, None, Some(&[2]), Some("operator"))
            .unwrap(),
        1
    );
    let rows = store.read_turns(ID, 0, None).unwrap();
    assert!(!rows[0].redacted);
    let erased = &rows[1];
    assert!(erased.redacted && erased.messages.is_empty());
    assert_eq!(
        (erased.turn, erased.created_at, &erased.actor),
        (sealed.turn, sealed.created_at, &sealed.actor)
    );
    let directory = store.conversation_dir(ID).unwrap();
    for file in ["records/2.jsonl", ".drafts/d.sealed"] {
        assert!(!directory.join(file).exists(), "{file}");
    }
    assert!(directory.join(".drafts/pending.state").exists());
    assert!(
        store.read_projections(ID, 0, None).unwrap()[0]
            .text
            .is_empty()
    );
    let acts = store.turn_redactions(None, None, 5).unwrap();
    assert_eq!(
        (acts[0].turns.as_slice(), acts[0].actor.as_deref()),
        (&[2][..], Some("operator"))
    );
    assert_eq!(store.redact_turns(ID, None, Some(&[2]), None).unwrap(), 1);
}

#[test]
fn erase_all_reaches_pending_only_and_committed_conversations() {
    let (_home, store) = enabled("records-erase-pending");
    let items = stream(&source(1000));
    send(&store, request(&items[..1])).unwrap();
    let data = messages(&[json!({"role":"user","content":"only a draft"})]);
    part(&store, ID, "pending", records_of(&data).unwrap(), false).unwrap();
    assert_eq!(store.redact_turns(ID, None, None, None).unwrap(), 0);
    assert_eq!(send(&store, request(&[])).unwrap().state, Staged::None);
    let directory = store.conversation_dir(ID).unwrap();
    assert!(!directory.join(".drafts").exists());
    send(
        &store,
        Restore {
            commit: true,
            ..request(&items)
        },
    )
    .unwrap();
    assert_eq!(store.redact_turns(ID, None, None, None).unwrap(), 3);
    assert!(
        store
            .read_turns(ID, 0, None)
            .unwrap()
            .iter()
            .all(|row| row.redacted)
    );
    for dir in [".restored", "records", ".drafts"] {
        assert!(!directory.join(dir).exists(), "{dir}");
    }
    assert_eq!(send(&store, request(&[])).unwrap().state, Staged::None);
}

#[test]
fn interrupted_erasure_reopens_with_one_complete_trail_act() {
    use crate::store::FileStore;
    use crate::store::erasure::publish::STOP;
    for boundary in 1..=7 {
        let (home, store) = enabled("records-erase-interrupted");
        let original = source(1000);
        send(
            &store,
            Restore {
                commit: true,
                ..request(&stream(&original))
            },
        )
        .unwrap();
        STOP.with(|n| n.set(boundary));
        let halted = store.redact_turns(ID, None, Some(&[3]), Some("operator"));
        STOP.with(|n| n.set(0));
        assert!(halted.unwrap_err().text().contains("stopped after erase"));
        drop(store);
        let store = FileStore::open(home.path()).unwrap();
        let projections = store.read_projections(ID, 0, None).unwrap();
        let committed = boundary > 1;
        assert_eq!(projections[0].text.is_empty(), committed);
        assert_eq!(
            store.read_turns(ID, 0, None).unwrap()[2].redacted,
            committed
        );
        let acts = store.turn_redactions(None, None, 10).unwrap();
        assert_eq!(acts.len(), if committed { 2 } else { 1 });
        if committed {
            assert_eq!(
                acts.iter()
                    .filter(|a| a.actor.as_deref() == Some("operator") && a.turns == [3])
                    .count(),
                1
            );
        }
        let dir = store.conversation_dir(ID).unwrap();
        assert_eq!(dir.join("records/3.jsonl").exists(), !committed);
        for state in [".erase-pending", ".erase-preparing", ".erase-retired"] {
            assert!(!dir.join(state).exists());
        }
    }
}

#[test]
fn an_invalid_late_projection_keeps_every_original_file() {
    use std::fs;
    let (_home, store) = enabled("records-erase-refusal");
    send(
        &store,
        Restore {
            commit: true,
            ..request(&stream(&source(1000)))
        },
    )
    .unwrap();
    let dir = store.conversation_dir(ID).unwrap();
    let files = [
        "turns.jsonl",
        "projections.jsonl",
        "redactions.jsonl",
        "records/3.jsonl",
    ];
    fs::write(dir.join(files[1]), "malformed\n").unwrap();
    let read = || files.map(|f| fs::read(dir.join(f)).unwrap());
    let before = read();
    assert!(store.redact_turns(ID, None, None, None).is_err());
    assert_eq!(read(), before);
    assert!(!dir.join(".erase-pending").exists());
}

#[test]
fn a_selective_erase_preserves_a_later_seal_with_the_restore_id() {
    let (_home, store) = enabled("records-erase-seal-owner");
    let restore = request(&stream(&source(1000)));
    let draft = restore.restore.clone();
    send(
        &store,
        Restore {
            commit: true,
            ..restore
        },
    )
    .unwrap();
    let data = messages(&[json!({"role":"user","content":"later sealed turn"})]);
    part(&store, ID, &draft, records_of(&data).unwrap(), true).unwrap();
    store.redact_turns(ID, None, Some(&[1]), None).unwrap();
    assert_eq!(
        part(&store, ID, &draft, Vec::new(), false)
            .unwrap()
            .turn
            .unwrap()
            .turn,
        4
    );
    assert!(!store.read_turns(ID, 3, None).unwrap()[0].redacted);
}

#[test]
fn erasing_a_legacy_log_streams_turns_and_trail_numbers() {
    use crate::conversation::Turn;
    use crate::conversation::rows::render_turn;
    use crate::store::time::Stamp;
    use std::io::Write;
    let (_home, store) = enabled("records-erase-memory");
    let dir = store.conversation_dir(ID).unwrap();
    std::fs::create_dir_all(&dir).unwrap();
    let mut log = std::io::BufWriter::new(std::fs::File::create(dir.join("turns.jsonl")).unwrap());
    let mut row = Turn {
        conversation_id: ID.into(),
        turn: 0,
        actor: None,
        created_at: Stamp::now(),
        redacted: false,
        messages: messages(&[json!({"role":"user","content":"x".repeat(2048)})]),
    };
    for n in 1..=4096 {
        row.turn = n;
        log.write_all(render_turn(&row).as_bytes()).unwrap();
    }
    drop(log);
    let (result, peak) = super::memory::peak(|| store.redact_turns(ID, None, None, None));
    assert_eq!(result.unwrap(), 4096);
    assert!(
        peak < 256 * 1024,
        "8 MiB of turn content peaked at {peak} bytes"
    );
    assert_eq!(
        store.turn_redactions(None, None, 1).unwrap()[0].turns,
        (1..=4096).collect::<Vec<_>>()
    );
}

#[test]
fn sparse_out_of_order_rows_keep_their_trail_order_and_exact_coverage() {
    use crate::conversation::Turn;
    use crate::conversation::rows::render_turn;
    use crate::store::time::Stamp;
    use std::fs;
    let (_home, store) = enabled("records-erase-sparse");
    let dir = store.conversation_dir(ID).unwrap();
    fs::create_dir_all(&dir).unwrap();
    let mut row = Turn {
        conversation_id: ID.into(),
        turn: 0,
        actor: None,
        created_at: Stamp::now(),
        redacted: false,
        messages: messages(&[json!({"role":"user","content":"held"})]),
    };
    let mut log = String::new();
    for turn in [7, 2, 10] {
        row.turn = turn;
        log.push_str(&render_turn(&row));
    }
    fs::write(dir.join("turns.jsonl"), log.trim_end()).unwrap();
    let projection = |turn, span| Projection {
        turn,
        span,
        text: "held".into(),
        kind: "epoch".into(),
    };
    store
        .append_projections(ID, &[projection(3, 2), projection(6, 2), projection(9, 3)])
        .unwrap();
    assert_eq!(store.redact_turns(ID, Some(7), None, None).unwrap(), 2);
    let visible = store.read_projections(ID, 0, None).unwrap();
    assert_eq!(
        visible.iter().map(|p| p.text.as_str()).collect::<Vec<_>>(),
        ["", "held", ""]
    );
    assert_eq!(
        store.turn_redactions(None, None, 1).unwrap()[0].turns,
        [7, 2]
    );
    store
        .append_turn(
            ID,
            messages(&[json!({"role":"user","content":"next"})]),
            None,
        )
        .unwrap();
    assert_eq!(store.read_turns(ID, 0, None).unwrap().len(), 4);
}
