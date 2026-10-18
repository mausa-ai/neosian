//! The streamable-HTTP wire as the SDK's `streamable_http.py` reads and
//! writes it (DESIGN §39.11): what a POST carries, the Accept and
//! Content-Type checks, the one SSE frame an answer rides, the JSON-RPC
//! refusals with the session header, and #370's refusal of a 2026-era
//! request in the spec's own shape.

use axum::body::{Body, to_bytes};
use axum::http::header::{ACCEPT, CACHE_CONTROL, CONNECTION, CONTENT_TYPE};
use axum::http::{HeaderMap, Method, Response, StatusCode};
use serde_json::{Map, Value, json};

use super::envelope::CEILING;
use crate::pyfmt::dumps;

pub const SESSION_HEADER: &str = "mcp-session-id";
const VERSION_HEADER: &str = "mcp-protocol-version";
const HANDSHAKE_VERSIONS: [&str; 4] = ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"];
pub const JSON: &str = "application/json";
const SSE: &str = "text/event-stream";
pub const INVALID_REQUEST: i64 = -32600;
pub const PARSE_ERROR: i64 = -32700;
pub const INTERNAL_ERROR: i64 = -32603;
const UNSUPPORTED_VERSION: i64 = -32022;

/// What one POST carries, as the SDK's message adapter reads it.
pub enum Message {
    Request {
        id: Value,
        method: String,
        params: Option<Map<String, Value>>,
    },
    /// A notification, or a response to a request this server never sent.
    Silent,
}

impl Message {
    pub fn of(value: &Value) -> Result<Self, &'static str> {
        let Value::Object(message) = value else {
            return Err("a JSON-RPC message is an object");
        };
        if message.get("jsonrpc").and_then(Value::as_str) != Some("2.0") {
            return Err("jsonrpc must be '2.0'");
        }
        let id = message.get("id");
        let Some(method) = message.get("method") else {
            let answered = message.contains_key("result") || message.contains_key("error");
            return match id {
                Some(_) if answered => Ok(Self::Silent),
                _ => Err("a message carries a method, a result or an error"),
            };
        };
        let Some(method) = method.as_str() else {
            return Err("method must be a string");
        };
        let params = match message.get("params") {
            None | Some(Value::Null) => None,
            Some(Value::Object(params)) => Some(params.clone()),
            Some(_) => return Err("params must be an object"),
        };
        // The adapter's union: a request's id is a string or an integer;
        // any other id is ignored by the notification model, which then
        // matches, so the message is taken and dropped (202).
        let integer = |n: &serde_json::Number| n.is_i64() || n.is_u64();
        match id {
            Some(id @ Value::String(_)) => Ok(Self::request(id, method, params)),
            Some(id @ Value::Number(n)) if integer(n) => Ok(Self::request(id, method, params)),
            _ => Ok(Self::Silent),
        }
    }

    fn request(id: &Value, method: &str, params: Option<Map<String, Value>>) -> Self {
        Self::Request {
            id: id.clone(),
            method: method.to_string(),
            params,
        }
    }
}

/// `check_accept_headers`: (json, sse), RFC 7231's wildcards honoured.
pub fn accepts(headers: &HeaderMap) -> (bool, bool) {
    let accept = headers
        .get(ACCEPT)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let types: Vec<String> = accept
        .split(',')
        .map(|t| {
            t.trim()
                .split(';')
                .next()
                .unwrap_or("")
                .trim()
                .to_lowercase()
        })
        .collect();
    let any = types.iter().any(|t| t == "*/*");
    let json = any || types.iter().any(|t| t == JSON || t == "application/*");
    let sse = any || types.iter().any(|t| t == SSE || t == "text/*");
    (json, sse)
}

/// The security middleware's one check on a POST.
pub fn posts_json(headers: &HeaderMap) -> bool {
    headers
        .get(CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|value| value.to_lowercase().starts_with(JSON))
}

/// A 2026-era protocol version stamped on the request, if any: the
/// manager routes it to the single-exchange entry the binary lacks.
pub fn modern_era(headers: &HeaderMap) -> Option<String> {
    let version = headers.get(VERSION_HEADER)?.to_str().ok()?;
    (!HANDSHAKE_VERSIONS.contains(&version)).then(|| version.to_string())
}

/// One SSE frame as sse-starlette writes it.
pub fn frame(message: &Value) -> Vec<u8> {
    format!("event: message\r\ndata: {}\r\n\r\n", dumps(message, false)).into_bytes()
}

fn builder(status: StatusCode, session: Option<&str>) -> axum::http::response::Builder {
    let mut builder = Response::builder().status(status);
    if let Some(id) = session {
        builder = builder.header(SESSION_HEADER, id);
    }
    builder
}

/// `_create_error_response` and the manager's `_error_response`: a
/// JSON-RPC error with no id, the session header when one is held.
pub fn refusal(
    status: StatusCode,
    code: i64,
    message: &str,
    session: Option<&str>,
) -> Response<Body> {
    let body = json!({"jsonrpc": "2.0", "id": null, "error": {"code": code, "message": message}});
    builder(status, session)
        .header(CONTENT_TYPE, JSON)
        .body(Body::from(dumps(&body, false)))
        .expect("static headers and a body build")
}

/// 202 for a notification, 200 for a DELETE: no body, the session header.
pub fn empty(status: StatusCode, session: &str) -> Response<Body> {
    builder(status, Some(session))
        .header(CONTENT_TYPE, JSON)
        .body(Body::empty())
        .expect("static headers build")
}

/// An SSE response, `EventSourceResponse`'s headers.
pub fn streaming(session: &str, body: Body) -> Response<Body> {
    builder(StatusCode::OK, Some(session))
        .header(CONTENT_TYPE, SSE)
        .header(CACHE_CONTROL, "no-cache, no-transform")
        .header(CONNECTION, "keep-alive")
        .header("x-accel-buffering", "no")
        .body(body)
        .expect("static headers and a body build")
}

/// #370: the four versions the binary speaks, named in the spec's shape;
/// the request's id when a POST carries one.
pub async fn unsupported_era(requested: String, method: &Method, body: Body) -> Response<Body> {
    let mut id = Value::Null;
    if *method == Method::POST
        && let Ok(bytes) = to_bytes(body, CEILING).await
        && let Ok(Value::Object(message)) = serde_json::from_slice::<Value>(&bytes)
    {
        id = message.get("id").cloned().unwrap_or(Value::Null);
    }
    let body = json!({
        "jsonrpc": "2.0",
        "id": id,
        "error": {
            "code": UNSUPPORTED_VERSION,
            "message": "Unsupported protocol version",
            "data": {"supported": HANDSHAKE_VERSIONS, "requested": requested},
        },
    });
    Response::builder()
        .status(StatusCode::BAD_REQUEST)
        .header(CONTENT_TYPE, JSON)
        .body(Body::from(dumps(&body, false)))
        .expect("static headers and a body build")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mcp::rpc::{Answer, envelope};

    fn headers(pairs: &[(&'static str, &str)]) -> HeaderMap {
        let mut headers = HeaderMap::new();
        for (name, value) in pairs {
            headers.insert(*name, value.parse().unwrap());
        }
        headers
    }

    #[test]
    fn the_accept_and_content_type_checks_are_the_sdks() {
        let accept = |value: &str| accepts(&headers(&[("accept", value)]));
        assert_eq!(accept("application/json, text/event-stream"), (true, true));
        assert_eq!(accept("*/*"), (true, true));
        assert_eq!(accept("Application/*;q=0.9, text/*"), (true, true));
        assert_eq!(accept("application/json"), (true, false));
        assert_eq!(accepts(&headers(&[])), (false, false));
        let posts = |value: &str| posts_json(&headers(&[("content-type", value)]));
        assert!(posts("Application/JSON; charset=utf-8"));
        assert!(!posts("text/plain"));
        assert!(!posts_json(&headers(&[])));
        let era = |value: &str| modern_era(&headers(&[("mcp-protocol-version", value)]));
        assert_eq!(era("2025-06-18"), None);
        assert_eq!(era("2026-07-28"), Some("2026-07-28".to_string()));
        assert_eq!(era("nope"), Some("nope".to_string()));
        assert_eq!(modern_era(&headers(&[])), None);
    }

    #[test]
    fn a_message_is_read_as_the_adapter_reads_it() {
        let request = json!({"jsonrpc": "2.0", "id": 1, "method": "ping"});
        assert!(matches!(
            Message::of(&request),
            Ok(Message::Request { id, method, params: None }) if id == 1 && method == "ping"
        ));
        let named = json!({"jsonrpc": "2.0", "id": "a", "method": "x", "params": {"k": 1}});
        assert!(matches!(
            Message::of(&named),
            Ok(Message::Request {
                params: Some(_),
                ..
            })
        ));
        for silent in [
            json!({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json!({"jsonrpc": "2.0", "id": 3, "result": {}}),
            json!({"jsonrpc": "2.0", "id": 3, "error": {"code": 1, "message": "m"}}),
            json!({"jsonrpc": "2.0", "id": 1.5, "method": "ping"}),
            json!({"jsonrpc": "2.0", "id": null, "method": "ping"}),
        ] {
            assert!(matches!(Message::of(&silent), Ok(Message::Silent)));
        }
        for (invalid, why) in [
            (json!([1]), "a JSON-RPC message is an object"),
            (json!({"id": 1, "method": "ping"}), "jsonrpc must be '2.0'"),
            (
                json!({"jsonrpc": "2.0", "id": 1}),
                "a message carries a method, a result or an error",
            ),
            (
                json!({"jsonrpc": "2.0", "id": 1, "method": 2}),
                "method must be a string",
            ),
            (
                json!({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": 1}),
                "params must be an object",
            ),
        ] {
            assert_eq!(Message::of(&invalid).err(), Some(why));
        }
    }

    #[test]
    fn the_frame_and_the_refusals_are_sse_starlettes_and_the_sdks() {
        let answer = envelope(&json!(1), Answer::Result(json!({"ok": "é"})));
        assert_eq!(
            frame(&answer),
            b"event: message\r\ndata: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"ok\":\"\xc3\xa9\"}}\r\n\r\n"
        );
        let refused = refusal(
            StatusCode::NOT_FOUND,
            INVALID_REQUEST,
            "Session not found",
            None,
        );
        assert_eq!(refused.status(), StatusCode::NOT_FOUND);
        assert!(refused.headers().get(SESSION_HEADER).is_none());
        let held = refusal(
            StatusCode::CONFLICT,
            INVALID_REQUEST,
            "Conflict",
            Some("abc"),
        );
        assert_eq!(held.headers().get(SESSION_HEADER).unwrap(), "abc");
        assert_eq!(held.headers().get(CONTENT_TYPE).unwrap(), JSON);
        let taken = empty(StatusCode::ACCEPTED, "abc");
        assert_eq!(taken.status(), StatusCode::ACCEPTED);
        assert_eq!(taken.headers().get(SESSION_HEADER).unwrap(), "abc");
    }

    #[tokio::test]
    async fn a_2026_era_request_is_refused_in_the_specs_shape() {
        let body = Body::from(r#"{"jsonrpc":"2.0","id":7,"method":"server/discover"}"#);
        let refused = unsupported_era("2026-07-28".to_string(), &Method::POST, body).await;
        assert_eq!(refused.status(), StatusCode::BAD_REQUEST);
        let bytes = to_bytes(refused.into_body(), CEILING).await.unwrap();
        let parsed: Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(
            parsed,
            json!({
                "jsonrpc": "2.0",
                "id": 7,
                "error": {
                    "code": -32022,
                    "message": "Unsupported protocol version",
                    "data": {"supported": HANDSHAKE_VERSIONS, "requested": "2026-07-28"},
                },
            })
        );
        let get = unsupported_era("nope".to_string(), &Method::GET, Body::empty()).await;
        let bytes = to_bytes(get.into_body(), CEILING).await.unwrap();
        let parsed: Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(parsed["id"], Value::Null);
        assert_eq!(parsed["error"]["data"]["requested"], "nope");
    }
}
