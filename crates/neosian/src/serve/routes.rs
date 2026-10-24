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
use super::envelope::{CEILING, Fail, json, text, unauthorized};
use super::params::Payload;
use super::{mcp, routes_conversation, routes_memory, routes_store};
use crate::version::VERSION;

/// The wire version this build speaks; `RemoteStore.connect` refuses another.
use crate::wire::WIRE_VERSION;

/// A route's body: the store, the typed payload and the stamped client.
pub type Handler = fn(&dyn Backend, &Payload, &str) -> Result<Value, Fail>;

pub struct Route {
    pub name: &'static str,
    pub handler: Handler,
    /// A route moving the store whole, refused to a constrained client.
    pub whole_store: bool,
}

fn table() -> impl Iterator<Item = &'static Route> {
    routes_memory::ROUTES
        .iter()
        .chain(routes_conversation::ROUTES.iter())
        .chain(routes_store::ROUTES.iter())
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
    });
    if let Some(grants) = &shared.clients.1 {
        capability["authorization"] = grants.capability();
    }
    json(StatusCode::OK, &capability)
}

async fn not_found() -> Response {
    text(StatusCode::NOT_FOUND, "Not Found")
}

async fn method_not_allowed() -> Response {
    text(StatusCode::METHOD_NOT_ALLOWED, "Method Not Allowed")
}

async fn endpoint(State(shared): State<super::State>, request: Request) -> Response {
    let name = request.uri().path().trim_start_matches("/v1/").to_string();
    let Some(route) = table().find(|route| route.name == name) else {
        return not_found().await;
    };
    let Some(client) = request.extensions().get::<Client>().cloned() else {
        return unauthorized();
    };
    let Ok(bytes) = to_bytes(request.into_body(), CEILING).await else {
        return Fail::oversize().response();
    };
    let Ok(payload) = serde_json::from_slice::<Value>(&bytes) else {
        return Fail::value("request body must be JSON").response();
    };
    let Value::Object(payload) = payload else {
        return Fail::value("request body must be an object").response();
    };
    // Only the route's actual target can authorize a store-wide read.
    // An ignored scope/conversation_id must not turn an unbounded search into a scoped one.
    if client.constrained()
        && matches!(
            name.as_str(),
            "conversation/search_turns" | "conversation/turn_redactions"
        )
        && !payload.get("conversations").is_some_and(Value::is_array)
    {
        return Fail::forbidden("the whole store").response();
    }
    if let Some(refusal) = auth::allowed(&client, &payload, route.whole_store) {
        return refusal.response();
    }
    if let Some(grants) = &shared.clients.1
        && let Some(refusal) = grants.allowed(&client.actor, &name, &payload, route.whole_store)
    {
        return refusal.response();
    }
    let handler = route.handler;
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
