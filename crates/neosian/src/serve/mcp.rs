//! `/mcp` over streamable HTTP (the SDK's session manager and transport
//! as `app.py` mounts them, DESIGN §18.6, §39.11): the handshake era
//! whole (#370). A POST carries one JSON-RPC message: a request is
//! answered as one SSE frame, a notification or a response is taken with
//! 202; `initialize` opens a session whose id every answer carries; GET
//! holds the session's one standing stream, DELETE ends it; a request
//! stamped with a 2026-era protocol version is refused by the spec's own
//! shape, which the SDK's client reads as a legacy advertisement and
//! falls back from. The bearer gate and the ceiling sit outside, as on
//! the Python door.

use std::convert::Infallible;
use std::sync::{Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use axum::body::{Body, Bytes, to_bytes};
use axum::extract::{Request, State};
use axum::http::header::ALLOW;
use axum::http::request::Parts;
use axum::http::{HeaderMap, Method, Response, StatusCode};
use futures_util::stream;
use tokio::sync::watch;

use super::auth::Client;
use super::envelope::{CEILING, Fail, text, unauthorized};
use super::sessions::{Full, Hold, Sessions, Streamless, hold, locked, open_stream};
use super::streamable::{
    INTERNAL_ERROR, INVALID_REQUEST, Message, PARSE_ERROR, SESSION_HEADER, accepts, empty, frame,
    modern_era, posts_json, refusal, streaming, unsupported_era,
};
use crate::mcp::rpc::{Answer, Handler, INVALID_PARAMS, envelope};
use crate::mcp::server::Server;
use crate::store::time::Stamp;

/// sse-starlette's keep-alive on the standing stream.
const PING: Duration = Duration::from_secs(15);
const MISSING_SESSION: &str = "Bad Request: Missing session ID";
const SESSION_NOT_FOUND: &str = "Session not found";

/// The surface: the one server every session dispatches to, the table,
/// and the signal that ends every standing stream at shutdown.
pub struct Surface {
    pub server: Mutex<Server>,
    pub sessions: Sessions,
    pub closing: watch::Sender<bool>,
}

impl Surface {
    pub fn new(server: Server) -> Self {
        Self {
            server: Mutex::new(server),
            sessions: Sessions::default(),
            closing: watch::channel(false).0,
        }
    }

    fn server(&self) -> MutexGuard<'_, Server> {
        self.server.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

fn not_found() -> Response<Body> {
    refusal(
        StatusCode::NOT_FOUND,
        INVALID_REQUEST,
        SESSION_NOT_FOUND,
        None,
    )
}

/// The manager: the era routed, the session found or minted, the
/// transport answering, an opening request that failed discarded.
pub async fn endpoint(State(shared): State<super::State>, request: Request) -> Response<Body> {
    let Some(surface) = shared.mcp.as_ref() else {
        return text(StatusCode::NOT_FOUND, "Not Found");
    };
    if request.extensions().get::<Client>().is_none() {
        return unauthorized();
    }
    let (parts, body) = request.into_parts();
    if let Some(requested) = modern_era(&parts.headers) {
        return unsupported_era(requested, &parts.method, body).await;
    }
    let now = Instant::now();
    let presented = parts
        .headers
        .get(SESSION_HEADER)
        .and_then(|v| v.to_str().ok())
        .map(str::to_string);
    let (id, opening) = match presented {
        Some(id) => (id, false),
        None => match locked(&surface.sessions).admit(now) {
            Ok(id) => (id, true),
            Err(Full) => {
                let message = "Too many open sessions";
                return refusal(
                    StatusCode::SERVICE_UNAVAILABLE,
                    INTERNAL_ERROR,
                    message,
                    None,
                );
            }
        },
    };
    let Some(held) = hold(&surface.sessions, &id, now) else {
        return not_found();
    };
    let response = transport(&shared, surface, &id, opening, parts, body).await;
    drop(held);
    if opening && response.status().as_u16() >= 400 {
        locked(&surface.sessions).terminate(&id);
    }
    response
}

/// `StreamableHTTPServerTransport._handle_request`, the session's id
/// known; `opening` is a request that presented none.
async fn transport(
    shared: &super::State,
    surface: &Surface,
    id: &str,
    opening: bool,
    parts: Parts,
    body: Body,
) -> Response<Body> {
    match parts.method {
        Method::POST => post(shared, surface, id, opening, &parts.headers, body).await,
        Method::GET => get(surface, id, opening, &parts.headers),
        Method::DELETE => delete(surface, id, opening),
        _ => {
            let message = "Method Not Allowed";
            let mut response = refusal(
                StatusCode::METHOD_NOT_ALLOWED,
                INVALID_REQUEST,
                message,
                Some(id),
            );
            let allowed = "GET, POST, DELETE".parse().expect("ascii");
            response.headers_mut().insert(ALLOW, allowed);
            response
        }
    }
}

async fn post(
    shared: &super::State,
    surface: &Surface,
    id: &str,
    opening: bool,
    headers: &HeaderMap,
    body: Body,
) -> Response<Body> {
    if !posts_json(headers) {
        return text(StatusCode::BAD_REQUEST, "Invalid Content-Type header");
    }
    let (json, sse) = accepts(headers);
    if !(json && sse) {
        let message =
            "Not Acceptable: Client must accept both application/json and text/event-stream";
        return refusal(
            StatusCode::NOT_ACCEPTABLE,
            INVALID_REQUEST,
            message,
            Some(id),
        );
    }
    let Ok(bytes) = to_bytes(body, CEILING).await else {
        return Fail::oversize().response();
    };
    let value = match serde_json::from_slice(&bytes) {
        Ok(value) => value,
        Err(error) => {
            let message = format!("Parse error: {error}");
            return refusal(StatusCode::BAD_REQUEST, PARSE_ERROR, &message, Some(id));
        }
    };
    let message = match Message::of(&value) {
        Ok(message) => message,
        Err(why) => {
            let message = format!("Validation error: {why}");
            return refusal(StatusCode::BAD_REQUEST, INVALID_PARAMS, &message, Some(id));
        }
    };
    let initializes = matches!(&message, Message::Request { method, .. } if method == "initialize");
    if opening && !initializes {
        return refusal(
            StatusCode::BAD_REQUEST,
            INVALID_REQUEST,
            MISSING_SESSION,
            Some(id),
        );
    }
    let Message::Request {
        id: rid,
        method,
        params,
    } = message
    else {
        return empty(StatusCode::ACCEPTED, id);
    };
    let admitted = match locked(&surface.sessions).find(id, Instant::now()) {
        Some(session) => session.gate.admit(&method),
        None => return not_found(),
    };
    let answer = match admitted {
        Err(refused) => refused,
        Ok(()) => {
            let shared = shared.clone();
            let reader = id.to_string();
            let handled = tokio::task::spawn_blocking(move || {
                let surface = shared
                    .mcp
                    .as_ref()
                    .expect("the route exists with the surface");
                let mut server = surface.server();
                server.set_reader(&reader);
                server.handle(&method, params.as_ref())
            })
            .await;
            handled.unwrap_or_else(|_| Answer::Error {
                code: INTERNAL_ERROR,
                message: "Internal server error".to_string(),
                data: None,
            })
        }
    };
    streaming(id, Body::from(frame(&envelope(&rid, answer))))
}

fn get(surface: &Surface, id: &str, opening: bool, headers: &HeaderMap) -> Response<Body> {
    if !accepts(headers).1 {
        let message = "Not Acceptable: Client must accept text/event-stream";
        return refusal(
            StatusCode::NOT_ACCEPTABLE,
            INVALID_REQUEST,
            message,
            Some(id),
        );
    }
    if opening {
        return refusal(
            StatusCode::BAD_REQUEST,
            INVALID_REQUEST,
            MISSING_SESSION,
            Some(id),
        );
    }
    match open_stream(&surface.sessions, id, Instant::now()) {
        Ok((held, closed)) => streaming(id, standing(held, closed, surface.closing.subscribe())),
        Err(Streamless::Conflict) => {
            let message = "Conflict: Only one SSE stream is allowed per session";
            refusal(StatusCode::CONFLICT, INVALID_REQUEST, message, Some(id))
        }
        Err(Streamless::Absent) => not_found(),
    }
}

fn delete(surface: &Surface, id: &str, opening: bool) -> Response<Body> {
    if opening {
        return refusal(
            StatusCode::BAD_REQUEST,
            INVALID_REQUEST,
            MISSING_SESSION,
            Some(id),
        );
    }
    locked(&surface.sessions).terminate(id);
    empty(StatusCode::OK, id)
}

/// The standing stream: nothing but sse-starlette's pings until the
/// session or the process ends; the hold keeps the session alive.
fn standing(held: Hold, closed: watch::Receiver<bool>, closing: watch::Receiver<bool>) -> Body {
    let ticks = tokio::time::interval_at(tokio::time::Instant::now() + PING, PING);
    let state = (held, closed, closing, ticks);
    Body::from_stream(stream::unfold(
        state,
        |(held, mut closed, mut closing, mut ticks)| async move {
            tokio::select! {
                _ = closed.changed() => None,
                _ = closing.changed() => None,
                _ = ticks.tick() => {
                    let ping = format!(": ping - {}\r\n\r\n", Stamp::now().iso().replace('T', " "));
                    Some((Ok::<Bytes, Infallible>(Bytes::from(ping)), (held, closed, closing, ticks)))
                }
            }
        },
    ))
}
