//! Per-client tokens and what each reaches (`server/tokens.py`,
//! `app.py`'s bearer gate, `routes.py`'s allowance, DESIGN §18.4, §20).
//! `NEOSIAN_SERVE_TOKEN` is one bare token (the client `client:default`)
//! or a table `actor[@scope-prefix][+id-prefix]=token[,…]`; every request
//! but `/health` presents one, compared timing-safely against every
//! entry; a hit stamps the client on the request. An allowance is an
//! allowlist of literal prefixes, matched against the parameter a request
//! names; a whole-store route and `/mcp` refuse a constrained client.

use axum::extract::{Request, State};
use axum::http::header::{AUTHORIZATION, CONTENT_LENGTH};
use axum::middleware::Next;
use axum::response::Response;
use serde_json::Value;

use super::envelope::{CEILING, Fail, unauthorized};
use super::params::Payload;
use crate::repr::repr;
use crate::store::grammar::Actor;

pub const DEFAULT_CLIENT: &str = "client:default";
const HINT: &str = "the value is one token, or actor[@scope-prefix][+id-prefix]=token[,…] \
                    (DESIGN §20, §18.4)";

/// A presented token's identity and what it may reach; both prefixes
/// None for a token with no allowance.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Client {
    pub actor: String,
    pub scopes: Option<String>,
    pub conversations: Option<String>,
}

impl Client {
    pub fn constrained(&self) -> bool {
        self.scopes.is_some() || self.conversations.is_some()
    }

    fn may_reach_scope(&self, scope: &str) -> bool {
        self.scopes
            .as_deref()
            .is_none_or(|prefix| scope.starts_with(prefix))
    }

    fn may_reach_conversation(&self, conversation_id: &str) -> bool {
        if !self.constrained() {
            return true;
        }
        self.conversations
            .as_deref()
            .is_some_and(|prefix| conversation_id.starts_with(prefix))
    }
}

/// The table: each entry the header bytes a hit presents and its client.
#[derive(Debug)]
pub struct Clients(Vec<(Vec<u8>, Client)>, pub Option<super::grants::Grants>);

impl Clients {
    pub fn with_grants(mut self) -> Result<Self, String> {
        let actors: Vec<_> = self.0.iter().map(|(_, c)| c.actor.as_str()).collect();
        self.1 = super::grants::Grants::load(&actors)?;
        Ok(self)
    }
}

/// `parse_clients`: `NEOSIAN_SERVE_TOKEN` to its table; refuses, never
/// guesses.
pub fn parse_clients(value: &str) -> Result<Clients, String> {
    if value.is_empty() {
        return Err(
            "a bearer token is required — the state process never serves \
                    unauthenticated (set NEOSIAN_SERVE_TOKEN)"
                .to_string(),
        );
    }
    let header = |token: &str| format!("Bearer {token}").into_bytes();
    if !value.contains('=') {
        let client = Client {
            actor: DEFAULT_CLIENT.to_string(),
            scopes: None,
            conversations: None,
        };
        return Ok(Clients(vec![(header(value), client)], None));
    }
    let mut clients: Vec<(Vec<u8>, Client)> = Vec::new();
    for (index, entry) in value.split(',').enumerate() {
        let index = index + 1;
        let (left, token) = match entry.split_once('=') {
            Some((left, token)) if !token.is_empty() => (left, token),
            _ => {
                return Err(format!(
                    "NEOSIAN_SERVE_TOKEN entry {index} is malformed: {HINT}"
                ));
            }
        };
        let client = client(left, index)?;
        let seen = clients
            .iter()
            .any(|(known, other)| *known == header(token) || other.actor == client.actor);
        if seen {
            return Err(format!(
                "NEOSIAN_SERVE_TOKEN entry {index} repeats a token or an actor"
            ));
        }
        clients.push((header(token), client));
    }
    Ok(Clients(clients, None))
}

/// `actor[@scope-prefix][+id-prefix]`: the allowance rides the actor side
/// so a token may still hold any character but `,`.
fn client(left: &str, index: usize) -> Result<Client, String> {
    let (head, conversations) = left
        .split_once('+')
        .map_or((left, None), |(head, ids)| (head, Some(ids)));
    let (actor, scopes) = head
        .split_once('@')
        .map_or((head, None), |(actor, scopes)| (actor, Some(scopes)));
    if let Err(reason) = Actor::parse(actor) {
        return Err(format!(
            "NEOSIAN_SERVE_TOKEN entry {index}: actor {} {reason}",
            repr(actor)
        ));
    }
    for (name, prefix) in [("scope", scopes), ("id", conversations)] {
        if prefix == Some("") {
            return Err(format!(
                "NEOSIAN_SERVE_TOKEN entry {index}: the {name} prefix is empty — omit the \
                 delimiter to leave that half unconstrained: {HINT}"
            ));
        }
    }
    Ok(Client {
        actor: actor.to_string(),
        scopes: scopes.map(str::to_string),
        conversations: conversations.map(str::to_string),
    })
}

/// `stamp`: the asserted client, its body-supplied tail under it.
pub fn stamp(client: &str, body: Option<&str>) -> String {
    match body {
        Some(body) => format!("{client}/{body}"),
        None => client.to_string(),
    }
}

/// `secrets.compare_digest`: the length may leak, no byte does.
fn same(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// The gate: every candidate is compared, since a short-circuit would
/// leak which token prefix matched; a miss is a plain 401; a hit stamps
/// the client. Inside the gate, a declared body over the ceiling is 413.
pub async fn bearer(
    State(shared): State<super::State>,
    mut request: Request,
    next: Next,
) -> Response {
    if request.uri().path() == "/health" {
        return next.run(request).await;
    }
    let supplied = request
        .headers()
        .get(AUTHORIZATION)
        .map_or(&[][..], |value| value.as_bytes());
    let mut client: Option<&Client> = None;
    for (expected, candidate) in &shared.clients.0 {
        if same(supplied, expected) {
            client = Some(candidate);
        }
    }
    let Some(client) = client.cloned() else {
        return unauthorized();
    };
    if (client.constrained() || shared.clients.1.is_some()) && request.uri().path() == "/mcp" {
        let what = "/mcp — the surface serves the operator's mounts, which no prefix can fence";
        return Fail::forbidden(what).response();
    }
    let declared = request
        .headers()
        .get(CONTENT_LENGTH)
        .and_then(|value| value.to_str().ok())
        .filter(|text| !text.is_empty() && text.bytes().all(|b| b.is_ascii_digit()))
        .and_then(|text| text.parse::<u128>().ok());
    if declared.is_some_and(|length| length > CEILING as u128) {
        return Fail::oversize().response();
    }
    request.extensions_mut().insert(client);
    next.run(request).await
}

/// `_allowed`: the parameter a request names is the thing the allowance
/// is checked against, a search's list id by id; a whole-store route and
/// a request naming nothing refuse a constrained client outright.
pub fn allowed(client: &Client, payload: &Payload, whole_store: bool) -> Option<Fail> {
    if !client.constrained() {
        return None;
    }
    if whole_store {
        return Some(Fail::forbidden("the whole store"));
    }
    let mut named = false;
    if let Some(scope) = payload.get("scope").and_then(Value::as_str) {
        named = true;
        if !client.may_reach_scope(scope) {
            return Some(Fail::forbidden(&format!("scope {}", repr(scope))));
        }
    }
    if let Some(id) = payload.get("conversation_id").and_then(Value::as_str) {
        named = true;
        if !client.may_reach_conversation(id) {
            return Some(Fail::forbidden(&format!("conversation {}", repr(id))));
        }
    }
    if let Some(ids) = payload.get("conversations").and_then(Value::as_array) {
        named = true;
        for id in ids.iter().filter_map(Value::as_str) {
            if !client.may_reach_conversation(id) {
                return Some(Fail::forbidden(&format!("conversation {}", repr(id))));
            }
        }
    }
    (!named).then(|| Fail::forbidden("the whole store"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_table_is_tokens_py_entry_for_entry() {
        let bare = parse_clients("tok").unwrap();
        assert_eq!(bare.0[0].0, b"Bearer tok");
        assert_eq!(bare.0[0].1.actor, DEFAULT_CLIENT);
        let table = parse_clients("client:alice@user:alice+alice-=t1,client:ops=t=2").unwrap();
        assert_eq!(
            table.0[0].1,
            Client {
                actor: "client:alice".to_string(),
                scopes: Some("user:alice".to_string()),
                conversations: Some("alice-".to_string()),
            }
        );
        assert_eq!(table.0[1].0, b"Bearer t=2");
        assert!(!table.0[1].1.constrained());
        for (value, why) in [
            ("a=", "NEOSIAN_SERVE_TOKEN entry 1 is malformed: "),
            (
                "client:a=t,=u",
                "NEOSIAN_SERVE_TOKEN entry 2: actor '' does not match",
            ),
            ("client:a=t,b", "NEOSIAN_SERVE_TOKEN entry 2 is malformed: "),
            (
                "Nope=t",
                "NEOSIAN_SERVE_TOKEN entry 1: actor 'Nope' does not match",
            ),
            (
                "client:a@=t",
                "NEOSIAN_SERVE_TOKEN entry 1: the scope prefix is empty",
            ),
            (
                "client:a+=t",
                "NEOSIAN_SERVE_TOKEN entry 1: the id prefix is empty",
            ),
            (
                "client:a=t,client:b=t",
                "NEOSIAN_SERVE_TOKEN entry 2 repeats a token or an actor",
            ),
            (
                "client:a=t,client:a=u",
                "NEOSIAN_SERVE_TOKEN entry 2 repeats a token or an actor",
            ),
        ] {
            assert!(
                parse_clients(value).unwrap_err().starts_with(why),
                "{value}"
            );
        }
    }

    #[test]
    fn the_allowance_is_an_allowlist_of_literal_prefixes() {
        let alice = &parse_clients("client:alice@user:alice+alice-=t").unwrap().0[0].1;
        let payload = |text: &str| serde_json::from_str::<Payload>(text).unwrap();
        assert!(allowed(alice, &payload(r#"{"scope": "user:alice2"}"#), false).is_none());
        let what = |fail: Option<Fail>| fail.unwrap().message;
        assert_eq!(
            what(allowed(alice, &payload(r#"{"scope": "user:bob"}"#), false)),
            "this token's allowance does not reach scope 'user:bob'"
        );
        assert_eq!(
            what(allowed(
                alice,
                &payload(r#"{"conversations": ["alice-1", "bob-1"]}"#),
                false
            )),
            "this token's allowance does not reach conversation 'bob-1'"
        );
        assert_eq!(
            what(allowed(alice, &payload(r#"{"query": "x"}"#), false)),
            "this token's allowance does not reach the whole store"
        );
        assert_eq!(
            what(allowed(alice, &payload(r#"{"scope": "user:alice"}"#), true)),
            "this token's allowance does not reach the whole store"
        );
        let reader = &parse_clients("client:reader@user:alice=t").unwrap().0[0].1;
        assert!(allowed(reader, &payload(r#"{"conversation_id": "alice-1"}"#), false).is_some());
        assert_eq!(stamp("client:default", Some("me")), "client:default/me");
        assert!(same(b"Bearer x", b"Bearer x") && !same(b"Bearer x", b"Bearer y"));
    }
}
