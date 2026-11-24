//! Visibility is the mapping of (conversation, owner, part). Open writes and
//! restore stages use the same underlying row shape as published turns.
use super::*;

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn only_mapped_owner_and_part_enter_hits_and_tool_names() {
    let db = Database::new();
    write(
        &db,
        "shared",
        "first",
        &[
            json!({"role":"assistant","tool_calls":[{"id":"call","name":"First","arguments":{}}]}),
            json!({"role":"tool","tool_call_id":"call","content":"public receipt A"}),
        ],
        true,
    );
    write(
        &db,
        "shared",
        "second",
        &[
            json!({"role":"assistant","tool_calls":[{"id":"call","name":"Second","arguments":{}}]}),
            json!({"role":"tool","tool_call_id":"call","content":"public receipt B"}),
        ],
        true,
    );
    write(
        &db,
        "shared",
        "pending",
        &[
            json!({"role":"assistant","tool_calls":[{"id":"call","name":"Private","arguments":{"hidden":"pending"}}]}),
            json!({"role":"tool","tool_call_id":"call","content":"hidden pending receipt"}),
        ],
        false,
    );

    // Make the two real sealed payloads the two parts of a committed restore.
    // Copy the open write to an additional unmapped part of the same owner.
    // A join on owner alone would expose the third part or mix the call names.
    db.execute(r#"
        UPDATE {{schema}}.turn_records SET
            owner=(SELECT owner FROM {{schema}}.record_turns WHERE turn=1),
            part=(SELECT turn FROM {{schema}}.record_turns m WHERE m.owner=turn_records.owner)
        WHERE owner IN (SELECT owner FROM {{schema}}.record_turns);
        UPDATE {{schema}}.record_turns SET
            owner=(SELECT owner FROM {{schema}}.record_turns WHERE turn=1), part=turn, draft='archive';
        INSERT INTO {{schema}}.turn_records
            SELECT r.conversation_id, m.owner, 3, r.seq,r.message,r.role,r.kind,r.content,
                   r.block,r.call,r.call_id,r.name,r.chunk,r.size,r.last,r.text,r.digest,r.neosian_format
            FROM {{schema}}.turn_records r
            JOIN {{schema}}.record_drafts d ON d.owner=r.owner AND d.name='pending'
            CROSS JOIN {{schema}}.record_turns m WHERE m.turn=1;
        -- A separate restore with a genuinely open header and uncommitted
        -- records exists beside the published restore and the open write.
        WITH active AS (
            INSERT INTO {{schema}}.record_drafts
                (conversation_id,kind,name,state,opened_at,touched_at,position,bytes)
                VALUES ('staged','restore','open','open',now(),now(),'{}',0) RETURNING owner
        ), stage AS (
            INSERT INTO {{schema}}.record_staging (conversation_id,owner,ord,item,body,digest)
                SELECT 'staged',owner,0,'turn','{"turn":1,"records":2}',decode('01','hex') FROM active
        )
        INSERT INTO {{schema}}.turn_records
            SELECT 'staged',a.owner,1,r.seq,r.message,r.role,r.kind,r.content,
                r.block,r.call,r.call_id,r.name,r.chunk,r.size,r.last,r.text,r.digest,r.neosian_format
            FROM {{schema}}.turn_records r JOIN {{schema}}.record_drafts d ON d.owner=r.owner
            CROSS JOIN active a WHERE d.name='pending';
    "#);
    let visible =
        db.query("SELECT count(*)::int AS n FROM {{schema}}.turn_records")[0].get::<_, i32>("n");
    assert_eq!(visible, 10, "two published parts and three unmapped pairs");
    assert_eq!(
        db.query("SELECT count(*)::int FROM {{schema}}.record_staging")[0].get::<_, i32>(0),
        1
    );
    for order in [Order::Newest, Order::Relevance] {
        assert!(
            db.store
                .search_records(&query("hidden", order))
                .unwrap()
                .is_empty()
        );
        let hits = db.store.search_records(&query("receipt", order)).unwrap();
        assert_eq!(positions(&hits), [("shared", 2, 1), ("shared", 1, 1)]);
        assert_eq!(
            hits.iter().map(|h| h.name.as_deref()).collect::<Vec<_>>(),
            [Some("Second"), Some("First")]
        );
    }
}
