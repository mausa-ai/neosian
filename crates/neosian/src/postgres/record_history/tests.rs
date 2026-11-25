//! The direct history APIs share the route's ranking but pass only full turns.
use super::*;
use crate::conversation::message::{self, Message};
use crate::conversation::turn_records::{Write, records_of};
use crate::postgres::records_conformance::Database;
use crate::wire::encode;
use serde_json::{Value, json};

fn ranked(store: &PostgresStore, request: &Request) -> Vec<(Value, Option<u64>)> {
    store
        .rank_turns(request)
        .unwrap()
        .into_iter()
        .map(|(turn, score)| (encode::turn(&turn), score))
        .collect()
}

fn prompt(text: &str) -> Message {
    message::from_json(&json!({"role":"user","content":text})).unwrap()
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn bounded_native_facts_use_whole_string_case_folding() {
    let db = Database::new();
    let text = "ΟΣ anchor";
    db.store
        .append_turn("legacy", vec![prompt(text)], None)
        .unwrap();
    db.store
        .write_records(
            "stored",
            Write {
                draft: "done".into(),
                records: records_of(&[prompt(text)]).unwrap(),
                seal: true,
                ..Write::default()
            },
            &limits::Records::load().unwrap(),
        )
        .unwrap();
    // Both sources match "anchor" independently of the database collation.
    // Whole-string lowercase yields final sigma (ς), unlike scalar folding.
    for second in ["ος", "οσ"] {
        let request = Request {
            terms: vec!["anchor".into(), second.into()],
            ids: None,
            limit: 2,
            order: Order::Relevance,
            since: None,
            before: None,
        };
        let hits = db.store.rank_turns(&request).unwrap();
        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0].1, hits[1].1, "{second}");
        assert_ne!(hits[0].0.conversation_id, hits[1].0.conversation_id);
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn large_losing_turns_do_not_consume_the_outgoing_row_budget() {
    if std::env::var(limits::RESPONSE).as_deref() != Ok("4096") {
        let test = std::thread::current();
        assert!(
            std::process::Command::new(std::env::current_exe().unwrap())
                .args(["--exact", test.name().unwrap(), "--ignored"])
                .env(limits::RESPONSE, "4096")
                .status()
                .unwrap()
                .success()
        );
        return;
    }
    let db = Database::new();
    let messages: Vec<_> = (0..20)
        .map(|i| {
            let word = if i == 0 { "needle" } else { "other" };
            prompt(&format!("{word} {}", "x".repeat(700)))
        })
        .collect();
    db.store
        .write_records(
            "big-native",
            Write {
                draft: "done".into(),
                records: records_of(&messages).unwrap(),
                seal: true,
                ..Write::default()
            },
            &limits::Records::load().unwrap(),
        )
        .unwrap();
    // A pre-existing format-1 row can exceed this reader's current limit.
    db.store
        .append_turn(
            "big-legacy",
            vec![prompt(&format!("needle {}", "x".repeat(14_000)))],
            None,
        )
        .unwrap();
    db.store
        .append_turn("winner", vec![prompt("needle needle needle")], None)
        .unwrap();
    let mut request = Request {
        terms: vec!["needle".into()],
        ids: None,
        limit: 1,
        order: Order::Relevance,
        since: None,
        before: None,
    };
    let best = db.store.rank_turns(&request).unwrap();
    assert_eq!(best.len(), 1);
    assert_eq!(best[0].0.conversation_id, "winner");
    for id in ["big-legacy", "big-native"] {
        request.ids = Some(vec![id.into()]);
        assert!(
            db.store
                .rank_turns(&request)
                .unwrap_err()
                .text()
                .contains(limits::RESPONSE)
        );
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn direct_search_keeps_the_winner_limit_and_stops_before_the_next_turn() {
    if std::env::var(limits::TURN_BYTES).as_deref() != Ok("65536") {
        let test = std::thread::current();
        let child = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", test.name().unwrap(), "--ignored"])
            .env(limits::TURN_BYTES, "65536")
            .status()
            .unwrap();
        assert!(child.success());
        return;
    }
    let db = Database::new();
    let limits = limits::Records::load().unwrap();
    let native = |draft: &str, text: &str| {
        db.store
            .write_records(
                "history",
                Write {
                    draft: draft.into(),
                    seal: true,
                    records: records_of(&[prompt(text)]).unwrap(),
                    ..Write::default()
                },
                &limits,
            )
            .unwrap();
    };
    native("small", "needle");
    db.store
        .append_turn("history", vec![prompt("needle")], None)
        .unwrap();
    native("large", &"needle ".repeat(10_000));

    // Newest uses stored timestamps. Seals use the database clock and a
    // whole-turn append uses the client clock, which can run behind it.
    db.execute(
        "WITH stamps AS (
             UPDATE {{schema}}.turns SET created_at = TIMESTAMPTZ '2026-01-01 00:00:00+00'
                 + turn * INTERVAL '1 second' WHERE conversation_id='history'
             RETURNING conversation_id, turn, created_at
         )
         UPDATE {{schema}}.record_turns m SET created_at=s.created_at FROM stamps s
         WHERE (m.conversation_id,m.turn)=(s.conversation_id,s.turn)",
    );

    let mut request = Request {
        terms: vec!["needle".into()],
        ids: None,
        limit: 2,
        order: Order::Newest,
        since: None,
        before: None,
    };
    let direct = db.store.search_turns(&request.terms, None, 2).unwrap();
    assert_eq!(direct.iter().map(|t| t.turn).collect::<Vec<_>>(), [2]);
    assert_eq!(
        ranked(&db.store, &request),
        [(encode::turn(&direct[0]), None)]
    );

    request.order = Order::Relevance;
    request.limit = 3;
    let mut expected = Vec::new();
    let mut omitted = Vec::new();
    let digest = db
        .store
        .rank_hits(&request, &Resume::Fresh, Mode::Each, &mut |hit, score| {
            match hit {
                Hit::Turn(turn) => expected.push((encode::turn(&turn), score)),
                Hit::Omitted(last) => omitted.push((last.turn, score)),
            }
            Ok(true)
        })
        .unwrap();
    assert!(digest.is_some());
    assert_eq!(expected.len(), 2);
    assert!(expected.iter().all(|(_, score)| score.is_some()));
    assert!(matches!(omitted.as_slice(), [(3, Some(_))]));
    assert_eq!(ranked(&db.store, &request), expected);
    let refused = db
        .store
        .rank_each(&request, &Resume::Fresh, Mode::Each, &mut |_, _| {
            Err(Error::Value("stop requested".into()))
        })
        .unwrap_err();
    assert!(matches!(refused, Error::Value(text) if text == "stop requested"));

    // An ordinary visitor can stop after the first answerable turn.
    // The next winner is intentionally unreadable, so an eager read fails.
    db.execute(
        "UPDATE {{schema}}.turn_records r SET digest=decode(repeat('00',32),'hex')
        FROM {{schema}}.record_turns m WHERE m.conversation_id='history' AND m.turn=1
        AND (r.conversation_id,r.owner,r.part)=(m.conversation_id,m.owner,m.part)",
    );
    let mut seen = Vec::new();
    db.store
        .search_each(&request.terms, None, 3, &mut |turn| {
            seen.push(turn.turn);
            Ok(false)
        })
        .unwrap();
    assert_eq!(seen, [2]);
    assert!(matches!(
        db.store.search_turns(&request.terms, None, 3),
        Err(Error::Conversation(refused)) if refused.code == "agent_conversation_format_unsupported"
    ));
}
