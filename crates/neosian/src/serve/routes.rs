//! The app (`server/app.py`, `routes.py`'s `endpoint`, DESIGN §18.6): one
//! router carrying `/health`, the handshake and the base/optional POST
//! routes, the bearer gate outermost, the body read under the ceiling, the
//! payload typed as an object, the allowance checked, then one blocking
//! store call off the runtime's workers. An unknown path and a wrong
//! method answer Starlette's plain text.

use axum::Router;
use axum::body::to_bytes;
use axum::extract::{Request, State};
use axum::http::StatusCode;
use axum::middleware;
use axum::response::Response;
use axum::routing::{any, get, post};
use serde_json::{Value, json};

use super::auth::{self, Client};
use super::backend::Backend;
use super::envelope::{Fail, json, text, unauthorized};
use super::grants::Grants;
use super::params::Payload;
use super::{mcp, routes_conversation, routes_memory, routes_records, routes_store};
use crate::version::VERSION;

/// The wire version this build speaks; `RemoteStore.connect` refuses another.
use crate::wire::WIRE_VERSION;

/// A route's body: the store, the typed payload and the stamped client. A
/// whole answer is one small value; a spooled one is written item by item
/// and sent after the store is released (DESIGN §40.46, #417).
pub enum Handler {
    Whole(fn(&dyn Backend, &Payload, &str) -> Result<Value, Fail>),
    Spooled(fn(&dyn Backend, &Payload, &str, &mut super::spool::Writer) -> Result<(), Fail>),
}

pub struct Route {
    pub name: &'static str,
    pub handler: Handler,
    /// A route moving the store whole, refused to a constrained client.
    pub whole_store: bool,
    /// The most bytes a body may hold, checked while it is read.
    pub ceiling: usize,
}

fn table() -> impl Iterator<Item = &'static Route> {
    routes_memory::ROUTES
        .iter()
        .chain(routes_conversation::ROUTES.iter())
        .chain(routes_records::ROUTES.iter())
        .chain(routes_store::ROUTES.iter())
}

/// The body ceiling of the route at `path`; the general one elsewhere.
pub fn ceiling(path: &str) -> usize {
    let name = path.strip_prefix("/v1/");
    let route = table().find(|route| Some(route.name) == name);
    route.map_or(super::envelope::CEILING, |route| route.ceiling)
}

pub fn router(shared: super::State) -> Router {
    let mut router = Router::new()
        .route("/health", get(health))
        .route("/v1/capabilities", get(capabilities));
    for route in table() {
        router = router.route(&format!("/v1/{}", route.name), post(endpoint));
    }
    if shared.mcp.is_some() {
        router = router.route("/mcp", any(mcp::endpoint));
    }
    router
        .fallback(not_found)
        .method_not_allowed_fallback(method_not_allowed)
        .layer(middleware::from_fn_with_state(shared.clone(), auth::bearer))
        .with_state(shared)
}

async fn health() -> Response {
    json(StatusCode::OK, &json!({"status": "ok"}))
}

/// The handshake: what the store can do, and who the token makes the caller.
async fn capabilities(State(shared): State<super::State>, request: Request) -> Response {
    let Some(client) = request.extensions().get::<Client>() else {
        return unauthorized();
    };
    let mut capability = json!({
        "wire_version": WIRE_VERSION,
        "neosian_version": VERSION,
        "backend": shared.store.backend(),
        "supports_optimistic_concurrency": shared.store.supports_optimistic_concurrency(),
        "pageable": true,
        "erasable": true,
        "client": client.actor,
        "memory_search": {"version": 1},
        "memory_ranking": {"version": 1},
        "memory_passages": {"version": 1},
        "history_ranking": {"version": 1},
        "knowledge_policy": {"version": 1},
        "size_paging": {"version": 1},
    });
    if let Some(grants) = &shared.clients.1 {
        capability["authorization"] = grants.capability();
    }
    let store = shared.store.clone();
    match tokio::task::spawn_blocking(move || store.supports_changes()).await {
        Ok(Ok(true)) => capability["change_feed"] = json!({"version":1}),
        Ok(Ok(false)) => {}
        Ok(Err(error)) => return Fail::from(error).response(),
        Err(_) => return Fail::internal().response(),
    }
    // Turn records version 1 (DESIGN §40.54 item 8.1): the last key, so a
    // store without records answers the bytes it always did. Files read
    // their marker on every handshake; Postgres answers what it detected.
    let store = shared.store.clone();
    match tokio::task::spawn_blocking(move || store.supports_records()).await {
        Ok(Ok(true)) => capability["turn_records"] = json!({"version":1}),
        Ok(Ok(false)) => {}
        Ok(Err(error)) => return Fail::from(error).response(),
        Err(_) => return Fail::internal().response(),
    }
    json(StatusCode::OK, &capability)
}

async fn not_found() -> Response {
    text(StatusCode::NOT_FOUND, "Not Found")
}

async fn method_not_allowed() -> Response {
    text(StatusCode::METHOD_NOT_ALLOWED, "Method Not Allowed")
}

/// Every authorization of a request, before its handler touches the
/// store: the token's allowance, then the client's grants.
fn authorized(
    client: &Client,
    grants: Option<&Grants>,
    route: &Route,
    payload: &Payload,
) -> Option<Fail> {
    // Only the route's actual target can authorize a store-wide read.
    // An ignored scope/conversation_id must not turn an unbounded search into a scoped one.
    if client.constrained()
        && matches!(
            route.name,
            "conversation/search_turns"
                | "conversation/turn_redactions"
                | "conversation/search_records"
        )
        && !payload.get("conversations").is_some_and(Value::is_array)
    {
        return Some(Fail::forbidden("the whole store"));
    }
    if let Some(refusal) = auth::allowed(client, payload, route.whole_store) {
        return Some(refusal);
    }
    grants.and_then(|grants| grants.allowed(&client.actor, route.name, payload, route.whole_store))
}

async fn endpoint(State(shared): State<super::State>, request: Request) -> Response {
    let name = request.uri().path().trim_start_matches("/v1/").to_string();
    let Some(route) = table().find(|route| route.name == name) else {
        return not_found().await;
    };
    let Some(client) = request.extensions().get::<Client>().cloned() else {
        return unauthorized();
    };
    let Ok(bytes) = to_bytes(request.into_body(), route.ceiling).await else {
        return Fail::oversize(route.ceiling).response();
    };
    let Ok(payload) = serde_json::from_slice::<Value>(&bytes) else {
        return Fail::value("request body must be JSON").response();
    };
    let Value::Object(payload) = payload else {
        return Fail::value("request body must be an object").response();
    };
    let grants = shared.clients.1.as_ref();
    if let Some(refusal) = authorized(&client, grants, route, &payload) {
        return refusal.response();
    }
    let handler = match route.handler {
        Handler::Whole(handler) => handler,
        Handler::Spooled(handler) => {
            return super::spool::respond(Box::new(move |writer| {
                handler(shared.store.as_ref(), &payload, &client.actor, writer)
            }))
            .await;
        }
    };
    let answered = tokio::task::spawn_blocking(move || {
        handler(shared.store.as_ref(), &payload, &client.actor)
    })
    .await;
    match answered {
        Ok(Ok(value)) => json(StatusCode::OK, &value),
        Ok(Err(fail)) => fail.response(),
        Err(_) => Fail::internal().response(),
    }
}

#[cfg(test)]
#[path = "routes_tests.rs"]
mod tests;
