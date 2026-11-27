use super::*;
use crate::conversation::message::from_json;
use crate::store::time::Stamp;

fn turn(messages: serde_json::Value) -> Turn {
    let messages = messages
        .as_array()
        .unwrap()
        .iter()
        .map(|message| from_json(message).unwrap())
        .collect();
    Turn {
        conversation_id: "s-1".into(),
        turn: 3,
        messages,
        created_at: Stamp::parse("2026-11-20T10:00:00Z").unwrap(),
        actor: Some("me".into()),
        redacted: false,
    }
}

fn search(query: &str, kinds: Option<Vec<Kind>>, order: Order, limit: usize) -> RecordSearch {
    RecordSearch::new(query, kinds, order, None, None, limit).unwrap()
}

#[test]
fn kinds_take_the_five_searchable_kinds_once_and_refuse_the_rest_by_name() {
    let kinds = parse_kinds(["tool_result", "reply", "tool_result"]).unwrap();
    assert_eq!(kinds, vec![Kind::ToolResult, Kind::Reply]);
    assert_eq!(kind_names(&kinds), "tool_result, reply");
    let all = ["prompt", "reply", "tool_call", "tool_result", "system"];
    assert_eq!(parse_kinds(all).unwrap().len(), 5);
    for opaque in ["reasoning", "attachment", "extra"] {
        assert_eq!(
            parse_kinds([opaque]).unwrap_err(),
            format!(
                "kind '{opaque}' is never searched; kinds takes prompt, reply, tool_call, tool_result and system"
            )
        );
    }
    assert!(
        parse_kinds(["Prompt"])
            .unwrap_err()
            .starts_with("unknown kind 'Prompt'; ")
    );
    assert!(
        parse_kinds([])
            .unwrap_err()
            .starts_with("kinds needs at least one of ")
    );
}

fn sample() -> Turn {
    turn(serde_json::json!([
        {"role": "user", "content": "deploy the cache"},
        {"role": "assistant", "content": null, "tool_calls": [
            {"id": "c1", "name": "deploy_cache", "arguments": {"target": "web"}}
        ]},
        {"role": "tool", "content": "cache ok\ndeploy ok", "tool_call_id": "c1"},
        {"role": "assistant", "content": "All good.", "reasoning": "deploy cache thinking"},
    ]))
}

#[test]
fn newest_derives_records_holding_every_term_in_record_order() {
    let mut hits = Vec::new();
    derive(
        &sample(),
        &search("CACHE deploy", None, Order::Newest, 10),
        &mut hits,
    )
    .unwrap();
    let places: Vec<(u64, Kind, Option<&str>)> = hits
        .iter()
        .map(|hit| (hit.seq, hit.kind, hit.name.as_deref()))
        .collect();
    // The call matches on its name; the reasoning record is never a hit.
    assert_eq!(
        places,
        vec![
            (0, Kind::Prompt, None),
            (1, Kind::ToolCall, Some("deploy_cache")),
            (2, Kind::ToolResult, Some("deploy_cache")),
        ]
    );
    assert!(hits.iter().all(|hit| hit.score.is_none() && hit.turn == 3));
    assert_eq!(hits[2].excerpt, (0, "cache ok\ndeploy ok".to_string()));
    assert_eq!(hits[1].excerpt, (0, "{\"target\":\"web\"}".to_string()));
}

#[test]
fn relevance_takes_one_term_and_the_limit_and_kinds_bound_the_hits() {
    let mut hits = Vec::new();
    let any = search("good cache", None, Order::Relevance, 10);
    derive(&sample(), &any, &mut hits).unwrap();
    let seqs: Vec<u64> = hits.iter().map(|hit| hit.seq).collect();
    assert_eq!(seqs, vec![0, 1, 2, 3]);
    let mut hits = Vec::new();
    derive(
        &sample(),
        &search("good cache", None, Order::Relevance, 2),
        &mut hits,
    )
    .unwrap();
    assert_eq!(hits.len(), 2);
    let mut hits = Vec::new();
    let replies = search("good cache", Some(vec![Kind::Reply]), Order::Relevance, 10);
    derive(&sample(), &replies, &mut hits).unwrap();
    assert_eq!(
        hits.iter().map(|hit| hit.kind).collect::<Vec<_>>(),
        vec![Kind::Reply]
    );
}

#[test]
fn a_turn_records_cannot_carry_is_refused_before_any_hit() {
    let long = "c".repeat(1_025);
    let unsplit = turn(serde_json::json!([
        {"role": "user", "content": "cache"},
        {"role": "tool", "content": "cache", "tool_call_id": long},
    ]));
    let mut hits = Vec::new();
    assert!(
        derive(
            &unsplit,
            &search("cache", None, Order::Newest, 5),
            &mut hits
        )
        .is_err()
    );
    assert!(hits.is_empty());
}

#[test]
fn a_header_names_the_record_and_an_excerpt_is_one_line() {
    let mut hits = Vec::new();
    derive(&sample(), &search("ok", None, Order::Newest, 5), &mut hits).unwrap();
    assert_eq!(
        header(&hits[0]),
        "[s-1 #3.2 tool_result deploy_cache] 2026-11-20T10:00:00Z me"
    );
    assert_eq!(excerpt(&hits[0]), "cache ok deploy ok");
    let mut late = hits[0].clone();
    late.excerpt = (41, "a\n b".into());
    late.actor = None;
    late.name = None;
    assert_eq!(
        header(&late),
        "[s-1 #3.2 tool_result] 2026-11-20T10:00:00Z -"
    );
    assert_eq!(excerpt(&late), "… a b");
}
