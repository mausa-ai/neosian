//! Admit a concurrent update after native membership takes its snapshot,
//! but before legacy membership or either winner reread. Exercise the
//! public store method without adding a synchronization hook to the search.
use super::Database;
use crate::conversation::message;
use crate::conversation::rank::Order;
use crate::conversation::turn_records::{RecordHit, RecordSearch, Write, records_of};
use crate::limits::Records;
use serde_json::json;
use std::thread;
use std::time::{Duration, Instant};

fn ordered_hits(hits: Vec<RecordHit>) -> Vec<(u64, Option<String>, String)> {
    let mut rows: Vec<_> = hits
        .into_iter()
        .map(|hit| (hit.turn, hit.actor, hit.excerpt.1))
        .collect();
    rows.sort_unstable();
    rows
}

fn observe(order: Order) {
    let db = Database::new();
    let prompt = |s: &str| message::from_json(&json!({"role":"user", "content":s})).unwrap();
    db.store
        .append_turn(
            "snap",
            vec![prompt("needle legacy")],
            Some("cli:before".into()),
        )
        .unwrap();
    db.store
        .write_records(
            "snap",
            Write {
                draft: "done".into(),
                records: records_of(&[prompt("needle native")]).unwrap(),
                seal: true,
                actor: Some("cli:before".into()),
                ..Write::default()
            },
            &Records::load().unwrap(),
        )
        .unwrap();
    // A view restricted to this test schema blocks at the native candidate's
    // time projection. The writer can still update the underlying tuples.
    db.execute(
        "ALTER TABLE {{schema}}.turns RENAME TO baseline_turns;
        CREATE FUNCTION {{schema}}.pause_at_time(timestamptz) RETURNS timestamptz
        LANGUAGE plpgsql STABLE AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock_shared('{{schema}}'::regnamespace::oid::bigint);
            RETURN $1;
        END $$;
        CREATE VIEW {{schema}}.turns AS
        SELECT conversation_id, turn, messages, {{schema}}.pause_at_time(created_at) AS created_at,
               neosian_format, actor, search_text, redacted
        FROM {{schema}}.baseline_turns",
    );
    db.execute("SELECT pg_advisory_lock('{{schema}}'::regnamespace::oid::bigint)");
    let store = db.another_store();
    let search = RecordSearch::new("needle", None, order, None, None, 10).unwrap();
    let during = search.clone();
    let reader = thread::spawn(move || store.search_records(&during).map_err(|e| e.text()));
    let end = Instant::now() + Duration::from_secs(15);
    let blocked = loop {
        if !db
            .query(
                "SELECT 1 FROM pg_stat_activity
                     WHERE pg_backend_pid() = ANY(pg_blocking_pids(pid))",
            )
            .is_empty()
        {
            break true;
        }
        if reader.is_finished() || Instant::now() >= end {
            break false;
        }
        thread::sleep(Duration::from_millis(10));
    };
    db.execute(
        "UPDATE {{schema}}.baseline_turns SET actor = 'cli:after',
        messages = CASE WHEN neosian_format = 1 THEN
            jsonb_set(messages, '{0,content}', to_jsonb('updated'::text)) ELSE messages END",
    );
    db.execute("SELECT pg_advisory_unlock('{{schema}}'::regnamespace::oid::bigint)");
    let observed = reader.join().unwrap().unwrap();
    assert!(blocked, "search never reached the synchronized candidate");
    let before = Some(String::from("cli:before"));
    assert_eq!(
        ordered_hits(observed),
        vec![
            (1, before.clone(), "needle legacy".into()),
            (2, before, "needle native".into()),
        ]
    );
    let latest = db.another_store().search_records(&search).unwrap();
    assert_eq!(
        ordered_hits(latest),
        vec![(2, Some("cli:after".into()), "needle native".into()),]
    );
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn search_membership_and_winners_use_the_same_snapshot_across_formats() {
    for order in [Order::Newest, Order::Relevance] {
        observe(order);
    }
}
