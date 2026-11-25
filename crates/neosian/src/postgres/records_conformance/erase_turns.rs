//! Erasure through the store APIs on a mixed PostgreSQL and FileStore conversation.
use super::Database;
use super::restore_stage::{ID, LIMITS, NAME, header, message, request, rows};
use crate::conversation::Projection;
use crate::conversation::rank::Order;
use crate::conversation::turn_records::{
    ReadItem, RecordRead, RecordSearch, Restore, Staged, State, Write, records_of,
};
use crate::store::FileStore;
use crate::testing::Scratch;
use crate::wire::encode;

macro_rules! both {
    ($files:expr, $pg:expr, |$s:ident| $body:block) => {{
        [
            {
                let $s = $files;
                $body
            },
            {
                let $s = $pg;
                $body
            },
        ]
    }};
}

fn write(name: &str, text: Option<&str>, seal: bool) -> Write {
    Write {
        draft: name.into(),
        records: text.map_or_else(Vec::new, |s| records_of(&[message("user", s)]).unwrap()),
        seal,
        ..Write::default()
    }
}

fn archived() -> Restore {
    let mut r = request(NAME);
    for (turn, time, text) in [
        (4, "2026-02-01T00:00:00Z", Some("needle four")),
        (7, "2026-03-01T00:00:00Z", None),
        (19, "2026-04-01T00:00:00Z", Some("needle nineteen")),
    ] {
        let records = text.map_or_else(Vec::new, |s| records_of(&[message("user", s)]).unwrap());
        r.turns.push(header(
            turn,
            time,
            records.len() as u64,
            text.map_or(0, |s| s.len()) as u64,
            text.is_none(),
        ));
        r.records
            .extend(records.into_iter().map(|record| (turn, record)));
    }
    r
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn selective_erasure_of_restored_and_legacy_turns_keeps_visible_siblings_and_unpublished_work() {
    let db = Database::new();
    let home = Scratch::new("erase-mixed-archive");
    let file = FileStore::open(home.path()).unwrap();
    file.enable_records().unwrap();
    let original = both!(&file, &db.store, |s| {
        let mut r = archived();
        r.commit = true;
        let restored = s.restore_records(ID, r, &LIMITS).unwrap();
        assert_eq!(restored.state, Staged::Committed);
        let native = write(NAME, Some("needle sealed"), true);
        let sealed = s.write_records(ID, native, &LIMITS).unwrap();
        assert_eq!(sealed.turn.unwrap().turn, 20);
        let messages = vec![message("user", "needle legacy")];
        let legacy = s
            .append_turn(ID, messages, Some("cli:owner".into()))
            .unwrap();
        assert_eq!(legacy.turn, 21);
        s.write_records(ID, write("pending", Some("needle pending"), false), &LIMITS)
            .unwrap();
        let entries = [
            (5, 2, "covers 4"),
            (12, 3, "in gap"),
            (20, 2, "covers survivors"),
            (21, 1, "covers 21"),
        ]
        .map(|(turn, span, text)| Projection {
            turn,
            span,
            text: text.into(),
            kind: "epoch".into(),
        });
        s.append_projections(ID, &entries).unwrap();
        let visible = s.read_turns(ID, 0, None).unwrap();
        visible.iter().map(encode::turn).collect::<Vec<_>>()
    });
    let ids = [ID.to_string()];
    let retained: Vec<_> = rows(&db, "turn_records")
        .into_iter()
        .filter(|r| r["part"] != 4)
        .collect();
    both!(&file, &db.store, |s| {
        assert_eq!(
            s.redact_turns(ID, None, Some(&[21, 4, 4, 7, 8]), Some("operator"))
                .unwrap(),
            3
        );
    });
    assert_eq!(rows(&db, "turn_records"), retained);
    let file = FileStore::open(home.path()).unwrap();
    let reopened = db.another_store();
    let after = both!(&file, &reopened, |s| {
        let probe = |name| {
            s.write_records(ID, write(name, None, false), &LIMITS)
                .unwrap()
        };
        assert_eq!(s.conversations().unwrap(), [ID]);
        assert_eq!(s.last_turn_number(ID).unwrap(), 21);
        let projections = s.read_projections(ID, 0, None).unwrap();
        assert_eq!(
            projections
                .iter()
                .map(|p| p.text.as_str())
                .collect::<Vec<_>>(),
            ["", "in gap", "covers survivors", ""]
        );
        let acts = s.turn_redactions(Some(&ids), None, 10).unwrap();
        assert_eq!(acts.len(), 1);
        assert_eq!(
            (acts[0].turns.as_slice(), acts[0].actor.as_deref()),
            (&[4, 7, 21][..], Some("operator"))
        );
        let mut search = RecordSearch::new("needle", None, Order::Newest, None, None, 10).unwrap();
        search.conversations = Some(ids.to_vec());
        let records = s.search_records(&search).unwrap();
        let history = s.search_turns(&["needle".into()], Some(&ids), 10).unwrap();
        assert_eq!(records.iter().map(|r| r.turn).collect::<Vec<_>>(), [20, 19]);
        assert_eq!(history.iter().map(|r| r.turn).collect::<Vec<_>>(), [20, 19]);
        let mut parts = Vec::new();
        assert!(
            s.read_records(ID, &RecordRead::default(), &mut |item| {
                if let ReadItem::Record { turn, .. } = item {
                    parts.push(turn);
                }
                Ok(())
            })
            .unwrap()
            .is_none()
        );
        assert_eq!(parts, [19, 20]);
        assert_eq!(
            s.restore_records(ID, request(NAME), &LIMITS).unwrap().state,
            Staged::Committed
        );
        assert_eq!(probe(NAME).turn.unwrap().turn, 20);
        assert_eq!(probe("pending").state, State::Open);
        let visible = s.read_turns(ID, 0, None).unwrap();
        visible.iter().map(encode::turn).collect::<Vec<_>>()
    });
    for (before, after) in original.into_iter().zip(after) {
        assert_eq!(before.len(), 5);
        assert_eq!(after.len(), 5);
        for (mut expected, actual) in before.into_iter().zip(after) {
            if matches!(expected["turn"].as_u64(), Some(4 | 7 | 21)) {
                expected["messages"] = serde_json::json!([]);
                expected["redacted"] = true.into();
            }
            assert_eq!(actual, expected);
        }
    }
    both!(&file, &reopened, |s| {
        assert_eq!(
            s.redact_turns(ID, None, Some(&[7, 4, 21]), Some("again"))
                .unwrap(),
            3
        );
        assert_eq!(
            s.redact_turns(ID, None, Some(&[11, 11]), Some("no match"))
                .unwrap(),
            0
        );
        let trail = s.turn_redactions(Some(&ids), None, 10).unwrap();
        assert_eq!(trail.len(), 2);
        assert_eq!(trail[0].actor.as_deref(), Some("again"));
        assert_eq!(trail[0].turns, [4, 7, 21]);
        assert_eq!(
            s.append_turn(ID, vec![message("user", "after")], None)
                .unwrap()
                .turn,
            22
        );
    });
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn through_and_an_explicit_list_naming_every_turn_preserve_work_and_receipts_until_full_erasure() {
    let db = Database::new();
    let home = Scratch::new("erase-has-selectors");
    let file = FileStore::open(home.path()).unwrap();
    file.enable_records().unwrap();
    let ids = [ID.to_owned()];
    both!(&file, &db.store, |s| {
        let pending = || {
            s.write_records(ID, write("pending", None, false), &LIMITS)
                .unwrap()
                .state
        };
        let receipt = || s.restore_records(ID, request(NAME), &LIMITS).unwrap();
        let mut r = archived();
        r.commit = true;
        let done = s.restore_records(ID, r, &LIMITS).unwrap();
        s.write_records(ID, write("pending", Some("private"), false), &LIMITS)
            .unwrap();
        for (through, named, count) in [
            (None, Some(&[][..]), 0),
            (None, Some(&[i64::MAX][..]), 0),
            (None, Some(&[i64::MAX, 4, 4, 7, 19][..]), 3),
            (Some(i64::MAX), None, 3),
        ] {
            assert_eq!(s.redact_turns(ID, through, named, None).unwrap(), count);
            assert_eq!(receipt().digest, done.digest);
            assert_eq!(pending(), State::Open);
        }
        let acts = s.turn_redactions(Some(&ids), None, 10).unwrap();
        assert_eq!(acts.len(), 2);
        assert!(acts.iter().all(|a| a.turns == [4, 7, 19]));
        assert_eq!(s.redact_turns(ID, None, None, Some("all")).unwrap(), 3);
        assert_eq!(receipt().state, Staged::None);
        assert_eq!(pending(), State::None);
        assert_eq!(s.last_turn_number(ID).unwrap(), 19);
        assert_eq!(s.turn_redactions(Some(&ids), None, 10).unwrap().len(), 3);
    });
    for table in [
        "record_drafts",
        "record_staging",
        "turn_records",
        "record_turns",
    ] {
        assert!(rows(&db, table).is_empty(), "{table}");
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn full_cleanup_reaches_unpublished_restores_with_or_without_a_visible_target() {
    for legacy in [false, true] {
        let db = Database::new();
        let home = Scratch::new("erase-pending-restore");
        let file = FileStore::open(home.path()).unwrap();
        file.enable_records().unwrap();
        let ids = [ID.to_owned()];
        both!(&file, &db.store, |s| {
            let restore = |id| s.restore_records(id, request(NAME), &LIMITS).unwrap().state;
            let draft = |id| {
                s.write_records(id, write(NAME, None, false), &LIMITS)
                    .unwrap()
                    .state
            };
            for id in [ID, "another"] {
                s.restore_records(id, archived(), &LIMITS).unwrap();
                s.write_records(id, write(NAME, Some("private draft"), false), &LIMITS)
                    .unwrap();
            }
            if legacy {
                s.append_turn(ID, vec![message("user", "legacy")], None)
                    .unwrap();
            }
            assert_eq!(
                s.redact_turns(ID, Some(1), None, None).unwrap(),
                usize::from(legacy)
            );
            assert_eq!(restore(ID), Staged::Open);
            assert_eq!(
                s.redact_turns(ID, None, None, None).unwrap(),
                usize::from(legacy)
            );
            assert_eq!(restore(ID), Staged::None);
            assert_eq!(draft(ID), State::None);
            let trail = s.turn_redactions(Some(&ids), None, 10).unwrap();
            assert_eq!(trail.len(), if legacy { 2 } else { 0 });
            assert!(trail.iter().all(|a| a.turns == [1]));
            assert_eq!(restore("another"), Staged::Open);
            assert_eq!(draft("another"), State::Open);
        });
        for table in ["record_drafts", "record_staging", "turn_records"] {
            let surviving = rows(&db, table);
            assert!(!surviving.is_empty(), "{table}");
            assert!(
                surviving
                    .iter()
                    .all(|row| row["conversation_id"] == "another")
            );
        }
    }
}
