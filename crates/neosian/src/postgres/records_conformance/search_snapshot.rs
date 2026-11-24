//! Admit a concurrent update after membership takes its snapshot and before
//! the winner reread. Exercise both public searches without a hook in either.
use super::Database;
use crate::conversation::rank::{Order, Request};
use crate::conversation::turn_records::{RecordSearch, Write, records_of};
use crate::conversation::{Error, message, search::turn_text};
use crate::limits::Records;
use crate::postgres::PostgresStore;
use serde_json::json;
use std::thread;
use std::time::{Duration, Instant};

fn observe(order: Order, history: bool) {
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
    let search = move |store: PostgresStore| -> Result<Vec<_>, Error> {
        let mut found: Vec<_> = if history {
            let request = Request {
                terms: vec!["needle".into()],
                ids: None,
                limit: 10,
                order,
                since: None,
                before: None,
            };
            store
                .rank_turns(&request)?
                .into_iter()
                .map(|(turn, _)| {
                    let text = turn_text(&turn);
                    (turn.turn, turn.actor, text)
                })
                .collect()
        } else {
            let request = RecordSearch::new("needle", None, order, None, None, 10).unwrap();
            store
                .search_records(&request)?
                .into_iter()
                .map(|hit| (hit.turn, hit.actor, hit.excerpt.1))
                .collect()
        };
        found.sort_unstable();
        Ok(found)
    };
    let reader = thread::spawn(move || search(store).map_err(|e| e.text()));
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
        search_text = CASE WHEN neosian_format = 1 THEN 'updated' ELSE search_text END,
        messages = CASE WHEN neosian_format = 1 THEN
            jsonb_set(messages, '{0,content}', to_jsonb('updated'::text)) ELSE messages END",
    );
    db.execute("SELECT pg_advisory_unlock('{{schema}}'::regnamespace::oid::bigint)");
    let observed = reader.join().unwrap().unwrap();
    assert!(blocked, "search never reached the synchronized candidate");
    let before = Some(String::from("cli:before"));
    assert_eq!(
        observed,
        vec![
            (1, before.clone(), "needle legacy".into()),
            (2, before, "needle native".into()),
        ]
    );
    let latest = search(db.another_store()).unwrap();
    assert_eq!(
        latest,
        vec![(2, Some("cli:after".into()), "needle native".into()),]
    );
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn search_membership_and_winners_use_the_same_snapshot_across_formats() {
    for order in [Order::Newest, Order::Relevance] {
        observe(order, false);
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn history_membership_and_winners_use_the_same_snapshot_across_formats() {
    for order in [Order::Newest, Order::Relevance] {
        observe(order, true);
    }
}
