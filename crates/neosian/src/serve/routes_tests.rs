//! The authorization of the two routes of `record_search` and of
//! `store/restore_records` (DESIGN §40.54 item 8.2), through the one
//! function every request passes before its handler: a token's allowance,
//! the rule that a constrained token names `conversations` on a search,
//! and the client's grants.

use serde_json::{Value, json};

use super::super::auth::Client;
use super::super::grants::Grants;
use super::{Route, authorized, table};

const SEARCH: &str = "conversation/search_records";
const SWEEP: &str = "store/sweep_records";
const RESTORE: &str = "store/restore_records";
const WHOLE: &str = "this token's allowance does not reach the whole store";
const GRANTS: &str = "this token's allowance does not reach the requested operation \
                      under this client's grants";
const TABLE: &str = r#"
grant_format = 1
[[clients]]
actor = "client:ops"
operator = true
[[clients.conversations]]
prefix = "s-"
read = true
[[clients]]
actor = "client:reader"
[[clients.conversations]]
prefix = "s-"
read = true
[[clients]]
actor = "client:writer"
[[clients.conversations]]
prefix = "s-"
append = true
"#;

fn route(name: &str) -> &'static Route {
    table().find(|route| route.name == name).expect("a route")
}

/// A token's client as the table spells it: `actor[@scopes][+ids]`.
fn client(entry: &str) -> Client {
    let (rest, conversations) = match entry.split_once('+') {
        Some((rest, prefix)) => (rest, Some(prefix.to_string())),
        None => (entry, None),
    };
    let (actor, scopes) = match rest.split_once('@') {
        Some((actor, prefix)) => (actor, Some(prefix.to_string())),
        None => (rest, None),
    };
    Client {
        actor: actor.to_string(),
        scopes,
        conversations,
    }
}

/// The refusal's message, none when the request is authorized.
fn refusal(entry: &str, grants: Option<&Grants>, name: &str, body: Value) -> Option<String> {
    let payload = body.as_object().expect("an object");
    let fail = authorized(&client(entry), grants, route(name), payload)?;
    assert_eq!((fail.status.as_u16(), fail.code), (403, "forbidden"));
    Some(fail.message)
}

#[test]
fn a_constrained_token_names_the_conversations_it_searches() {
    let limited = "client:limited@user:mine+mine-";
    let none = |body| refusal(limited, None, SEARCH, body);
    assert_eq!(none(json!({"query": "x", "conversations": []})), None);
    assert_eq!(
        none(json!({"query": "x", "conversations": ["mine-1", "mine-2"]})),
        None
    );
    assert_eq!(
        none(json!({"query": "x", "conversations": ["mine-1", "theirs-1"]})).unwrap(),
        "this token's allowance does not reach conversation 'theirs-1'"
    );
    // Absent, or anything but a list, is the whole store; no field the
    // route does not read stands in for the list.
    for body in [
        json!({"query": "x"}),
        json!({"query": "x", "conversations": null}),
        json!({"query": "x", "conversations": "mine-1"}),
        json!({"query": "x", "conversations": {"mine-1": true}}),
        json!({"query": "x", "conversation_id": "mine-1"}),
        json!({"query": "x", "scope": "user:mine"}),
        json!({"query": "x", "scope": "user:mine", "conversation_id": "mine-1"}),
    ] {
        assert_eq!(none(body.clone()).unwrap(), WHOLE, "{body}");
    }
    // A token with no allowance searches every conversation.
    for body in [
        json!({"query": "x"}),
        json!({"query": "x", "conversations": ["theirs-1"]}),
    ] {
        assert_eq!(refusal("client:ops", None, SEARCH, body), None);
    }
    // A token fenced to scopes alone reaches no conversation.
    let scoped = "client:scoped@user:mine";
    let fenced = refusal(scoped, None, SEARCH, json!({"conversations": ["mine-1"]}));
    assert_eq!(
        fenced.unwrap(),
        "this token's allowance does not reach conversation 'mine-1'"
    );
    assert_eq!(
        refusal(scoped, None, SEARCH, json!({"conversations": []})),
        None
    );
}

#[test]
fn grants_need_read_on_every_conversation_a_search_names() {
    let grants: Grants = toml::from_str(TABLE).unwrap();
    let under = |entry, body| refusal(entry, Some(&grants), SEARCH, body);
    let named = |ids: &[&str]| json!({"query": "x", "conversations": ids});
    assert_eq!(under("client:reader", named(&["s-1", "s-2"])), None);
    assert_eq!(under("client:reader", named(&[])), None);
    assert_eq!(under("client:ops", named(&["s-1"])), None);
    for (entry, body) in [
        ("client:reader", named(&["s-1", "t-1"])),
        ("client:writer", named(&["s-1"])),
        ("client:unknown", named(&["s-1"])),
        ("client:unknown", named(&[])),
        (
            "client:reader",
            json!({"query": "x", "conversations": ["s-1", 7]}),
        ),
        (
            "client:reader",
            json!({"query": "x", "conversation_id": "s-1"}),
        ),
        // Absent is denied to every actor under grants, the operator too.
        ("client:reader", json!({"query": "x"})),
        ("client:ops", json!({"query": "x"})),
        ("client:ops", json!({"query": "x", "conversations": null})),
    ] {
        assert_eq!(
            under(entry, body.clone()).unwrap(),
            GRANTS,
            "{entry} {body}"
        );
    }
    // The token's allowance is checked first and the grants intersect it.
    let fenced = "client:reader@user:mine+s-1";
    assert_eq!(under(fenced, named(&["s-1"])), None);
    assert_eq!(
        under(fenced, named(&["s-2"])).unwrap(),
        "this token's allowance does not reach conversation 's-2'"
    );
    assert_eq!(under(fenced, json!({"query": "x"})).unwrap(), WHOLE);
}

#[test]
fn the_sweep_is_the_operators_alone() {
    let grants: Grants = toml::from_str(TABLE).unwrap();
    for body in [json!({}), json!({"limit": 1, "write": true})] {
        assert_eq!(refusal("client:ops", None, SWEEP, body.clone()), None);
        assert_eq!(refusal("client:ops", Some(&grants), SWEEP, body), None);
    }
    // Nothing a constrained token names brings the sweep inside its
    // allowance, with grants or without, whatever they grant.
    for entry in [
        "client:ops@user:mine",
        "client:ops+s-",
        "client:reader@user:mine+s-",
    ] {
        for body in [
            json!({}),
            json!({"write": true}),
            json!({"conversation_id": "s-1"}),
            json!({"conversations": ["s-1"]}),
            json!({"conversations": []}),
            json!({"scope": "user:mine"}),
        ] {
            assert_eq!(refusal(entry, None, SWEEP, body.clone()).unwrap(), WHOLE);
            assert_eq!(refusal(entry, Some(&grants), SWEEP, body).unwrap(), WHOLE);
        }
    }
    for entry in ["client:reader", "client:writer", "client:unknown"] {
        for body in [json!({}), json!({"conversation_id": "s-1"})] {
            assert_eq!(
                refusal(entry, Some(&grants), SWEEP, body).unwrap(),
                GRANTS,
                "{entry}"
            );
        }
    }
}

#[test]
fn a_restore_in_parts_is_the_operators_alone() {
    let grants: Grants = toml::from_str(TABLE).unwrap();
    let named = json!({"conversation_id": "s-1", "restore": "r"});
    let bodies = [
        named.clone(),
        json!({"conversation_id": "s-1", "restore": "r", "discard": true}),
        json!({"conversation_id": "s-1", "restore": "r", "turns": [], "commit": true}),
        json!({"conversation_id": "s-1", "conversations": ["s-1"], "scope": "user:mine"}),
        json!({}),
    ];
    for body in &bodies {
        assert_eq!(refusal("client:ops", None, RESTORE, body.clone()), None);
        assert_eq!(
            refusal("client:ops", Some(&grants), RESTORE, body.clone()),
            None
        );
    }
    // A constrained token is refused whatever it names, the conversation
    // inside its allowance included, with grants or without.
    for entry in [
        "client:ops@user:mine",
        "client:ops+s-",
        "client:reader@user:mine+s-",
    ] {
        for body in &bodies {
            let none = refusal(entry, None, RESTORE, body.clone());
            assert_eq!(none.unwrap(), WHOLE, "{entry} {body}");
            let under = refusal(entry, Some(&grants), RESTORE, body.clone());
            assert_eq!(under.unwrap(), WHOLE, "{entry} {body}");
        }
    }
    // Under grants neither read nor append on the conversation is enough.
    for entry in ["client:reader", "client:writer", "client:unknown"] {
        for body in &bodies {
            let under = refusal(entry, Some(&grants), RESTORE, body.clone());
            assert_eq!(under.unwrap(), GRANTS, "{entry} {body}");
        }
    }
    let route = route(RESTORE);
    assert!(route.whole_store);
    assert_eq!(route.ceiling, 4_194_304);
    assert_eq!(super::ceiling("/v1/store/restore_records"), 4_194_304);
}

#[test]
fn the_rule_for_the_two_earlier_listing_routes_is_unchanged() {
    let limited = "client:limited@user:mine+mine-";
    for name in ["conversation/search_turns", "conversation/turn_redactions"] {
        let none = |body| refusal(limited, None, name, body);
        assert_eq!(none(json!({"conversations": ["mine-1"]})), None);
        assert_eq!(none(json!({"conversation_id": "mine-1"})).unwrap(), WHOLE);
        assert_eq!(none(json!({"scope": "user:mine"})).unwrap(), WHOLE);
    }
    // A route that names one conversation is not asked for a list.
    let read = "conversation/read_records";
    assert_eq!(
        refusal(limited, None, read, json!({"conversation_id": "mine-1"})),
        None
    );
}
