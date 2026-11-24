//! Live record-search contracts on isolated operator-installed schemas.
mod bounds;
mod queries;
mod visibility;

use crate::conversation::Turn;
use crate::conversation::message::{self, Message};
use crate::conversation::rank::Order;
use crate::conversation::turn_records::{
    Kind, RecordHit, RecordSearch, TEXT_BYTES, Write, records_of,
};
use crate::limits;
use crate::postgres::records_conformance::Database;
use crate::store::FileStore;
use crate::store::time::Stamp;
use crate::testing::Scratch;
use serde_json::{Value, json};

const T0: &str = "2026-05-01T10:00:00.123456Z";
const T1: &str = "2026-05-01T10:00:00.123457Z";

fn messages(data: &[Value]) -> Vec<Message> {
    data.iter()
        .map(|m| message::from_json(m).unwrap())
        .collect()
}

fn restore(db: &Database, id: &str, at: &str, data: &[Value]) {
    let turn = Turn {
        conversation_id: id.into(),
        turn: 1,
        actor: Some("source:legacy".into()),
        created_at: Stamp::parse(at).unwrap(),
        redacted: data.is_empty(),
        messages: messages(data),
    };
    db.store
        .restore_conversation(id, &[turn], &[], &[])
        .unwrap();
}

fn write(db: &Database, id: &str, draft: &str, data: &[Value], seal: bool) {
    let records = records_of(&messages(data)).unwrap();
    let part = Write {
        draft: draft.into(),
        records,
        seal,
        actor: Some("source:native".into()),
        ..Write::default()
    };
    db.store
        .write_records(id, part, &limits::Records::load().unwrap())
        .unwrap();
}

fn query(text: &str, order: Order) -> RecordSearch {
    RecordSearch::new(text, None, order, None, None, 100).unwrap()
}

fn positions(hits: &[RecordHit]) -> Vec<(&str, u64, u64)> {
    hits.iter()
        .map(|h| (h.conversation_id.as_str(), h.turn, h.seq))
        .collect()
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn mixed_turns_match_the_same_filestore_views_and_explicit_tie_order() {
    let db = Database::new();
    restore(
        &db,
        "a",
        T0,
        &[
            json!({"role":"user","content":"amber delta ledger"}),
            json!({"role":"assistant","content":null,"tool_calls":[{"id":"reused","name":"LegacyOp","arguments":{"verylong":"needle","q":"Z"}}]}),
            json!({"role":"tool","content":"old receipt","tool_call_id":"reused"}),
            json!({"role":"system","content":"ledger"}),
        ],
    );
    write(
        &db,
        "a",
        "new",
        &[
            json!({"role":"user","content":"amber ledger"}),
            json!({"role":"assistant","tool_calls":[{"id":"reused","name":"NativeOp","arguments":{"q":"elsewhere"}}]}),
            json!({"role":"tool","content":"amber delta ledger receipt","tool_call_id":"reused"}),
        ],
        true,
    );
    write(
        &db,
        "b",
        "only",
        &[
            json!({"role":"user","content":"amber delta ledger"}),
            json!({"role":"tool","content":"orphan receipt","tool_call_id":"reused"}),
        ],
        true,
    );
    restore(
        &db,
        "later",
        T1,
        &[json!({"role":"user","content":"ledger"})],
    );
    restore(&db, "redacted", T0, &[]);
    db.execute(&format!(
        "UPDATE {{{{schema}}}}.turns SET created_at='{T0}' WHERE neosian_format=2;
        UPDATE {{{{schema}}}}.record_turns SET created_at='{T0}'"
    ));

    // Build the oracle from what PostgreSQL actually stored. JSONB assigns its
    // own key order to the old call; the record codec then compacts that order.
    let home = Scratch::new("pg-search-reference");
    let files = FileStore::open(home.path()).unwrap();
    files.enable_records().unwrap();
    for id in ["a", "b", "later", "redacted"] {
        let turns = db.store.read_turns(id, 0, None).unwrap();
        files.restore_conversation(id, &turns, &[], &[]).unwrap();
    }
    let compare = |r: &RecordSearch| {
        let hits = db.store.search_records(r).unwrap();
        assert_eq!(hits, files.search_records(r).unwrap(), "{r:?}");
        hits
    };
    for term in [
        "amber delta",
        "ledger",
        "receipt",
        "LegacyOp",
        r#""q":"Z","verylong":"needle""#,
    ] {
        for order in [Order::Newest, Order::Relevance] {
            compare(&query(term, order));
        }
    }
    assert!(compare(&query("LegacyOp NativeOp", Order::Newest)).is_empty());
    let legacy = compare(&query(r#""q":"Z","verylong":"needle""#, Order::Newest));
    assert_eq!(positions(&legacy), [("a", 1, 1)]);
    assert_eq!(legacy[0].excerpt.1, r#"{"q":"Z","verylong":"needle"}"#);
    let hits = compare(&query("ledger", Order::Newest));
    assert_eq!(
        positions(&hits),
        [
            ("later", 1, 0),
            ("b", 1, 0),
            ("a", 2, 0),
            ("a", 2, 2),
            ("a", 1, 0),
            ("a", 1, 3)
        ]
    );
    assert!(hits.iter().all(|h| h.score.is_none()));
    let kinds = vec![Kind::ToolResult];
    let results =
        compare(&RecordSearch::new("receipt", Some(kinds), Order::Newest, None, None, 10).unwrap());
    let names: Vec<_> = results.iter().map(|h| h.name.as_deref()).collect();
    assert_eq!(names, [None, Some("NativeOp"), Some("LegacyOp")]);
    let mut limited = RecordSearch::new(
        "ledger",
        Some(vec![Kind::Prompt]),
        Order::Newest,
        Some(T0.into()),
        Some(T1.into()),
        2,
    )
    .unwrap();
    limited.conversations = Some(vec!["b".into(), "a".into(), "b".into(), "absent".into()]);
    assert_eq!(positions(&compare(&limited)), [("b", 1, 0), ("a", 2, 0)]);
    limited.conversations = Some(vec![]);
    assert!(compare(&limited).is_empty());
    limited.conversations = Some(vec!["../invalid".into()]);
    assert!(db.store.search_records(&limited).is_err());
    assert!(
        compare(&RecordSearch::new("amber", Some(vec![]), Order::Newest, None, None, 2).unwrap())
            .is_empty()
    );
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn literal_substrings_and_unicode_membership_follow_postgres_ilike() {
    let db = Database::new();
    // C is always present, even on an installation without ICU. Its lower()
    // cannot turn the dotted capital I into the two target Unicode scalars.
    db.execute("ALTER TABLE {{schema}}.turn_records ALTER COLUMN text TYPE text COLLATE \"C\"");
    let low: String = db.query("SELECT lower('İ' COLLATE \"C\")").remove(0).get(0);
    assert_ne!(low, "i\u{307}");
    let marked = json!({"role":"user","content":"İST Kelvin ΟΣ literal %_\\proof"});
    let decoy = json!({"role":"user","content":"plain literal XXZproof"});
    restore(&db, "old", T0, &[marked.clone(), decoy.clone()]);
    write(&db, "new", "d", &[marked, decoy], true);
    for term in [r"%_\proof", "%", "_", r"\"] {
        for order in [Order::Newest, Order::Relevance] {
            let hits = db.store.search_records(&query(term, order)).unwrap();
            assert_eq!(
                positions(&hits),
                [("new", 1, 0), ("old", 1, 0)],
                "literal search for {term:?}"
            );
        }
    }
    // A query term is already folded, but SQL decides membership. Native
    // text uses the column collation and the virtual legacy rows use the
    // database default. A candidate can have no occurrences under Rust's
    // per-scalar count, in which case its relevance score is zero.
    let cases = db.query(
        r#"SELECT term,
            'İST Kelvin ΟΣ' COLLATE "C" ILIKE '%' || term || '%' AS native,
            'İST Kelvin ΟΣ' ILIKE '%' || term || '%' AS legacy
        FROM (VALUES ('ist'), ('i̇st'), ('kelvin'), ('οσ')) AS terms(term)"#,
    );
    for row in cases {
        let term: &str = row.get("term");
        let native: bool = row.get("native");
        let legacy: bool = row.get("legacy");
        if term == "i̇st" {
            assert!(!native, "the C collation does not expand dotted I");
        }
        let mut expected = vec![];
        if native {
            expected.push(("new", 1, 0));
        }
        if legacy {
            expected.push(("old", 1, 0));
        }
        for order in [Order::Newest, Order::Relevance] {
            let hits = db.store.search_records(&query(term, order)).unwrap();
            assert_eq!(positions(&hits), expected, "ILIKE {term:?}");
            if term == "ist" && order == Order::Relevance {
                assert!(hits.iter().all(|hit| hit.score == Some(0)));
            }
        }
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn terms_on_opposite_sides_of_a_forced_cut_do_not_form_one_hit() {
    let db = Database::new();
    let text = format!("{}LEFTRIGHT", "x".repeat(TEXT_BYTES - 2 - 4));
    let data = [
        json!({"role":"user","content":text}),
        json!({"role":"assistant","content":"otherpiece"}),
    ];
    let parts = records_of(&messages(&data)).unwrap();
    assert!(parts[0].text.ends_with("LEFT") && parts[1].text == "RIGHT");
    restore(&db, "old", T0, &data);
    write(&db, "new", "d", &data, true);
    for term in ["leftright", "left right", "right otherpiece"] {
        assert!(
            db.store
                .search_records(&query(term, Order::Newest))
                .unwrap()
                .is_empty(),
            "{term}"
        );
    }
    let hits = db
        .store
        .search_records(&query("left right", Order::Relevance))
        .unwrap();
    assert_eq!(hits.len(), 4);
    for id in ["new", "old"] {
        let mut seqs: Vec<_> = hits
            .iter()
            .filter(|h| h.conversation_id == id)
            .map(|h| h.seq)
            .collect();
        seqs.sort_unstable();
        assert_eq!(seqs, [0, 1]);
    }
}
