//! The same stale owners, placements and transferable cursors on both reference stores.
mod boundaries;

use super::Database;
use super::restore_stage::{LIMITS, header, message, request, rows};
use crate::conversation::turn_records::{Restore, Staged, State, Write, records_of};
use crate::store::FileStore;
use crate::testing::Scratch;
use serde_json::{Value, json};
use std::fs;

const MIXED_CURSOR: &str = "WyJzd2VlcC12MSIsIk1peGVkIl0=";
const Z_CURSOR: &str = "WyJzd2VlcC12MSIsIloiXQ==";

fn write(name: &str, text: Option<&str>, seal: bool) -> Write {
    Write {
        draft: name.into(),
        records: text.map_or_else(Vec::new, |s| records_of(&[message("user", s)]).unwrap()),
        seal,
        ..Write::default()
    }
}

fn restore(name: &str, commit: bool) -> Restore {
    let records = records_of(&[message("user", "résumé")]).unwrap();
    Restore {
        turns: vec![header(3, "2024-05-06T07:08:09Z", 1, 8, false)],
        records: records.into_iter().map(|r| (3, r)).collect(),
        commit,
        ..request(name)
    }
}

fn snapshot(db: &Database) -> [Vec<Value>; 8] {
    [
        "conversations",
        "turns",
        "projections",
        "turn_redactions",
        "record_drafts",
        "record_staging",
        "record_turns",
        "turn_records",
    ]
    .map(|table| rows(db, table))
}

fn row(id: &str, drafts: usize, restores: usize, freed: u64) -> Value {
    json!({"conversation_id":id,"drafts":drafts,"restores":restores,"freed_bytes":freed})
}

fn page(items: &[Value], cursor: Option<&str>, write: bool) -> Value {
    json!({"conversations":items,"next_cursor":cursor,"write":write})
}

struct Pair {
    db: Database,
    file: FileStore,
    home: Scratch,
}
impl Pair {
    fn new() -> Self {
        let home = Scratch::new("pg-file-sweep");
        let file = FileStore::open(home.path()).unwrap();
        file.enable_records().unwrap();
        Self {
            db: Database::new(),
            file,
            home,
        }
    }

    fn write(&self, id: &str, name: &str, seal: bool) -> u64 {
        let open = || write(name, Some("piñata"), seal);
        let pg = self.db.store.write_records(id, open(), &LIMITS).unwrap();
        let file = self.file.write_records(id, open(), &LIMITS).unwrap();
        assert_eq!((pg.state, pg.stored_bytes), (file.state, file.stored_bytes));
        pg.stored_bytes
    }

    fn restore(&self, id: &str, name: &str, commit: bool) -> u64 {
        let part = restore(name, commit);
        let pg = self
            .db
            .store
            .restore_records(id, part.clone(), &LIMITS)
            .unwrap();
        assert_eq!(self.file.restore_records(id, part, &LIMITS).unwrap(), pg);
        pg.stored_bytes
    }

    fn stale(&self, id: &str, kind: &str, name: &str) {
        let path = if kind == "write" {
            self.home
                .path()
                .join("conversations")
                .join(id)
                .join(".drafts")
                .join(format!("{name}.state"))
        } else {
            self.home
                .path()
                .join(".records/restore")
                .join(id)
                .join(name)
                .join("state")
        };
        let mut state: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        state["touched_at"] = json!("2000-01-01T00:00:00Z");
        fs::write(path, serde_json::to_vec(&state).unwrap()).unwrap();
        self.db.execute(&format!(
            "UPDATE {{{{schema}}}}.record_drafts SET touched_at='2000-01-01T00:00:00Z'
            WHERE conversation_id='{id}' AND kind='{kind}' AND name='{name}'"
        ));
    }

    fn check_page(
        &self,
        limit: usize,
        file_after: Option<&str>,
        pg_after: Option<&str>,
        expected: Value,
    ) -> (Option<String>, Option<String>) {
        let apply = expected["write"].as_bool().unwrap();
        let file = self.file.sweep_records(limit, file_after, apply).unwrap();
        let pg = self.db.store.sweep_records(limit, pg_after, apply).unwrap();
        // Serialization equality also pins field order and the absence of extra fields.
        assert_eq!(serde_json::to_string(&pg).unwrap(), expected.to_string());
        assert_eq!(serde_json::to_string(&file).unwrap(), expected.to_string());
        (file.next_cursor, pg.next_cursor)
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn preview_apply_and_transferred_cursors_refund_only_open_stale_owners() {
    let p = Pair::new();
    // On case-folding filesystems the two differently spelled keys live in different trees.
    let lower = p.restore("mixed", "shared", false);
    let upper = p.write("Mixed", "shared", false);
    let left = p.write("Z", "shared", false);
    let right = p.write("Z", "second", false);
    let staged = p.restore("Z", "shared", false);
    p.write("Z", "keep", false);
    p.write("Z", "sealed", true);
    p.restore("A", "shared", true);
    let first = p.write("A", "shared", false);
    p.write("H-fresh", "shared", false);
    p.restore("P-closed", "shared", true);
    p.write("Y-closed", "shared", true);
    for (id, kind, name) in [
        ("A", "write", "shared"),
        ("Mixed", "write", "shared"),
        ("Z", "write", "shared"),
        ("Z", "write", "second"),
        ("Z", "restore", "shared"),
        ("mixed", "restore", "shared"),
    ] {
        p.stale(id, kind, name);
    }
    // Item 14 independently charges bookkeeping + one text record for this stale write.
    assert_eq!(first, 1024 + 256 + 4 + 6 + 7);
    let expected = [
        row("A", 1, 0, first),
        row("Mixed", 1, 0, upper),
        row("Z", 2, 1, left + right + staged),
        row("mixed", 0, 1, lower),
    ];
    let original = snapshot(&p.db);
    let (file_prev, pg_prev) = p.check_page(
        2,
        None,
        None,
        page(&expected[..2], Some(MIXED_CURSOR), false),
    );
    p.check_page(
        2,
        pg_prev.as_deref(),
        file_prev.as_deref(),
        page(&expected[2..], None, false),
    );
    p.check_page(100, None, None, page(&expected, None, false));
    assert_eq!(snapshot(&p.db), original);
    let (file_cut, pg_cut) = p.check_page(
        2,
        None,
        None,
        page(&expected[..2], Some(MIXED_CURSOR), true),
    );
    let after_first = snapshot(&p.db);
    for (before, after) in original.iter().zip(&after_first) {
        for old in before
            .iter()
            .filter(|r| r["conversation_id"] == "Z" || r["conversation_id"] == "mixed")
        {
            assert!(
                after.contains(old),
                "lookahead or later tuple was removed or rewritten"
            );
        }
    }
    // A cursor crosses backends, page size and operation, including after its owner was removed.
    let (file_next, pg_next) = p.check_page(
        1,
        pg_prev.as_deref(),
        file_cut.as_deref(),
        page(&expected[2..3], Some(Z_CURSOR), true),
    );
    p.check_page(
        1,
        pg_next.as_deref(),
        file_next.as_deref(),
        page(&expected[3..], None, true),
    );
    p.check_page(
        100,
        pg_cut.as_deref(),
        file_prev.as_deref(),
        page(&[], None, true),
    );
    p.check_page(1, None, None, page(&[], None, false));
    p.check_page(1, None, None, page(&[], None, true));
    let stale_owners: Vec<_> = original[4]
        .iter()
        .filter(|owner| {
            owner["touched_at"]
                .as_str()
                .unwrap()
                .starts_with("2000-01-01")
        })
        .map(|owner| owner["owner"].as_i64().unwrap())
        .collect();
    assert_eq!(stale_owners.len(), 6);
    for (before, remaining) in original.iter().zip(snapshot(&p.db)) {
        let expected: Vec<_> = before
            .iter()
            .filter(|row| {
                !row["owner"]
                    .as_i64()
                    .is_some_and(|n| stale_owners.contains(&n))
            })
            .collect();
        assert_eq!(remaining.iter().collect::<Vec<_>>(), expected);
    }
    let pg = p.db.another_store();
    let file = FileStore::open(p.home.path()).unwrap();
    for (id, name, state) in [
        ("A", "shared", State::None),
        ("Mixed", "shared", State::None),
        ("Z", "shared", State::None),
        ("Z", "keep", State::Open),
        ("Z", "sealed", State::Sealed),
        ("H-fresh", "shared", State::Open),
        ("Y-closed", "shared", State::Sealed),
    ] {
        assert_eq!(
            pg.write_records(id, write(name, None, false), &LIMITS)
                .unwrap()
                .state,
            state
        );
        assert_eq!(
            file.write_records(id, write(name, None, false), &LIMITS)
                .unwrap()
                .state,
            state
        );
    }
    for (id, state) in [
        ("Z", Staged::None),
        ("mixed", Staged::None),
        ("A", Staged::Committed),
        ("P-closed", Staged::Committed),
    ] {
        let status = pg.restore_records(id, request("shared"), &LIMITS).unwrap();
        assert_eq!(status.state, state);
        assert_eq!(
            file.restore_records(id, request("shared"), &LIMITS)
                .unwrap(),
            status
        );
    }
    assert_eq!(
        pg.conversations().unwrap(),
        ["A", "P-closed", "Y-closed", "Z"]
    );
    assert_eq!(file.conversations().unwrap(), pg.conversations().unwrap());
}
