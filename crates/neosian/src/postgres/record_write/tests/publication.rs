//! Physical observations of a multipart write on the database clock.
use super::*;

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn drafts_are_touched_and_headers_are_stamped_after_the_parts_landed() {
    let db = Database::new();
    db.execute("CREATE TABLE {{schema}}.landed (at timestamptz NOT NULL);
        CREATE FUNCTION {{schema}}.observe_part() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN PERFORM pg_sleep(0.03); INSERT INTO {{schema}}.landed VALUES (clock_timestamp()); RETURN NULL; END $$;
        CREATE TRIGGER observed AFTER INSERT ON {{schema}}.turn_records
        FOR EACH STATEMENT EXECUTE FUNCTION {{schema}}.observe_part()");
    let rows = data();
    send(&db.store, part("d", &rows[..1])).unwrap();
    let touched: bool = db.query(
        "SELECT touched_at >= (SELECT max(at) FROM {{schema}}.landed)
        FROM {{schema}}.record_drafts WHERE name='d'",
    )[0]
    .get(0);
    send(
        &db.store,
        Write {
            seal: true,
            ..part("d", &rows[1..])
        },
    )
    .unwrap();
    let sealed: bool = db.query(
        "SELECT t.created_at >= (SELECT max(at) FROM {{schema}}.landed)
        AND m.created_at=t.created_at AND c.created_at=t.created_at
        FROM {{schema}}.turns t JOIN {{schema}}.record_turns m USING (conversation_id, turn)
        JOIN {{schema}}.conversations c USING (conversation_id)",
    )[0]
    .get(0);
    assert_eq!((touched, sealed), (true, true));
}

fn unsupported(result: Result<Written, Error>) {
    let error = result.unwrap_err();
    let Error::Conversation(refused) = error else {
        panic!("{}", error.text())
    };
    assert_eq!(refused.code, "agent_conversation_format_unsupported");
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn repeat_ranges_and_sealed_receipts_refuse_damaged_storage() {
    let db = Database::new();
    let rows = data();
    send(&db.store, part("d", &rows)).unwrap();
    db.execute("UPDATE {{schema}}.turn_records SET neosian_format=3 WHERE seq=1");
    assert_eq!(send(&db.store, part("d", &rows[..1])).unwrap().next_seq, 3);
    unsupported(send(&db.store, part("d", &rows[1..2])));
    db.execute("UPDATE {{schema}}.turn_records SET neosian_format=1 WHERE seq=1");
    send(
        &db.store,
        Write {
            seal: true,
            ..part("d", &[])
        },
    )
    .unwrap();
    db.execute("UPDATE {{schema}}.turn_records SET text='bad' WHERE seq=1");
    unsupported(send(&db.store, part("d", &rows[1..2])));
    db.execute("UPDATE {{schema}}.turns SET neosian_format=9 WHERE turn=1");
    unsupported(send(&db.store, part("d", &[])));
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn a_split_json_piece_keeps_its_check_and_final_chunk_across_requests() {
    let db = Database::new();
    let message = message::from_json(&json!({
        "role":"assistant","content":null,
        "tool_calls":[{"id":"call","name":"tool","arguments":{"field":"v".repeat(94_000)}}]
    }))
    .unwrap();
    let rows = records_of(&[message]).unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[1].chunk, 1);
    let opened = send(&db.store, part("d", &rows[..1])).unwrap();
    assert_eq!(opened.next_seq, 1);
    let other = db.another_store();
    let mut broken = rows[1..].to_vec();
    assert!(broken[0].text.ends_with('}'));
    broken[0].text.pop();
    broken[0].text.push(']');
    assert!(send(&other, part("d", &broken)).is_err());
    assert_eq!(send(&db.store, part("d", &[])).unwrap().next_seq, 1);
    let sealed = send(
        &other,
        Write {
            seal: true,
            ..part("d", &rows[1..])
        },
    )
    .unwrap();
    assert_eq!(sealed.state, State::Sealed);
    let flags: Vec<bool> = db
        .query("SELECT last FROM {{schema}}.turn_records ORDER BY seq")
        .iter()
        .map(|row| row.get(0))
        .collect();
    assert_eq!(flags, [false, true]);
}
