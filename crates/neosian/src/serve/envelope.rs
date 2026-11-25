//! The §18.3 envelope and the plain answers beside it: a store refusal
//! crosses as 400 `{"error": {"code", "message", "details"}}`, a
//! mis-typed parameter and the ABCs' bare `ValueError` under the sentinel
//! `value_error`, an allowance under `forbidden` at 403, the ceiling at
//! 413; an infrastructure failure is the bare 500 the client propagates
//! raw (ledger #39). JSON is written as `JSONResponse` writes it: compact,
//! non-ASCII raw.

use axum::body::Body;
use axum::http::header::{CONTENT_TYPE, WWW_AUTHENTICATE};
use axum::http::{Response, StatusCode};
use serde_json::{Value, json};

use crate::conversation::message::Undecodable;
use crate::conversation::{self, Refused};
use crate::limits::RECORD_TURN_BYTES;
use crate::pyfmt::dumps;
use crate::repr::repr;
use crate::store::error::{self, MemoryError};

pub const VALUE_ERROR: &str = "value_error";
pub const FORBIDDEN: &str = "forbidden";
/// `MAX_REQUEST_BYTES`: the ceiling over every surface (IN-3) but the two
/// routes that take a turn or a conversation in parts.
pub const CEILING: usize = 64 * 1024 * 1024;
/// A `write_records` or `restore_records` body (§40.54 item 8.2).
pub const PART_CEILING: usize = 4 * 1024 * 1024;

/// What a route answers instead of its value. The details are boxed so
/// a handler's `Result` stays small on every path.
#[derive(Debug)]
pub struct Fail {
    pub status: StatusCode,
    pub code: &'static str,
    pub message: String,
    pub details: Box<Value>,
}

impl Fail {
    fn enveloped(status: StatusCode, code: &'static str, message: String, details: Value) -> Self {
        Self {
            status,
            code,
            message,
            details: Box::new(details),
        }
    }

    /// The caller's fault, in the ABCs' own words.
    pub fn value(message: impl Into<String>) -> Self {
        Self::enveloped(
            StatusCode::BAD_REQUEST,
            VALUE_ERROR,
            message.into(),
            json!({}),
        )
    }

    /// The backstop beneath the typed readers: a `KeyError` on a nested
    /// object, worded as Python words it.
    pub fn malformed(key: &str) -> Self {
        Self::value(format!("malformed request: {}", repr(key)))
    }

    /// Authenticated, outside the token's allowance (§18.4).
    pub fn forbidden(what: &str) -> Self {
        Self::enveloped(
            StatusCode::FORBIDDEN,
            FORBIDDEN,
            format!("this token's allowance does not reach {what}"),
            json!({}),
        )
    }

    /// A body over its route's ceiling, in the same envelope.
    pub fn oversize(ceiling: usize) -> Self {
        Self::enveloped(
            StatusCode::PAYLOAD_TOO_LARGE,
            VALUE_ERROR,
            format!("request body exceeds {ceiling} bytes"),
            json!({}),
        )
    }

    /// A conversation store's bare refusal. A turn larger than the store
    /// keeps is an operation budget answered 413 (§40.54 item 8.2); its
    /// prefix differs from the body ceiling's, so a client tells them apart.
    fn refused(sentence: String) -> Self {
        let bound = format!("resource limit {RECORD_TURN_BYTES} exceeded: ");
        let mut fail = Self::value(sentence);
        if fail.message.starts_with(&bound) {
            fail.status = StatusCode::PAYLOAD_TOO_LARGE;
        }
        fail
    }

    /// The driver's problem, never a store answer.
    pub fn internal() -> Self {
        Self::enveloped(
            StatusCode::INTERNAL_SERVER_ERROR,
            "",
            String::new(),
            json!({}),
        )
    }

    pub fn response(self) -> Response<Body> {
        if self.status == StatusCode::INTERNAL_SERVER_ERROR {
            return text(self.status, "Internal Server Error");
        }
        let body = json!({
            "error": {"code": self.code, "message": self.message, "details": *self.details}
        });
        json(self.status, &body)
    }
}

impl From<MemoryError> for Fail {
    fn from(error: MemoryError) -> Self {
        Self::enveloped(
            StatusCode::BAD_REQUEST,
            error.code,
            error.message,
            error.details,
        )
    }
}

impl From<Refused> for Fail {
    fn from(refused: Refused) -> Self {
        Self::enveloped(
            StatusCode::BAD_REQUEST,
            refused.code,
            refused.message,
            refused.details,
        )
    }
}

impl From<error::Error> for Fail {
    fn from(error: error::Error) -> Self {
        match error {
            error::Error::Memory(error) => error.into(),
            error::Error::Value(sentence) => Self::value(sentence),
            error::Error::Io(_) => Self::internal(),
        }
    }
}

impl From<conversation::Error> for Fail {
    fn from(error: conversation::Error) -> Self {
        match error {
            conversation::Error::Conversation(refused) => refused.into(),
            conversation::Error::Memory(error) => error.into(),
            conversation::Error::Value(sentence) => Self::refused(sentence),
            conversation::Error::Io(_) => Self::internal(),
        }
    }
}

/// A spool's failure: its budget refusal is the caller's fault, as every
/// operation budget is; anything else has no envelope.
impl From<std::io::Error> for Fail {
    fn from(error: std::io::Error) -> Self {
        error::Error::from(error).into()
    }
}

impl From<Undecodable> for Fail {
    fn from(error: Undecodable) -> Self {
        match error {
            Undecodable::Key(key) => Self::malformed(key),
            Undecodable::Value(sentence) | Undecodable::Type(sentence) => Self::value(sentence),
        }
    }
}

/// `JSONResponse`: compact separators, non-ASCII raw, the one header.
pub fn json(status: StatusCode, value: &Value) -> Response<Body> {
    Response::builder()
        .status(status)
        .header(CONTENT_TYPE, "application/json")
        .body(Body::from(dumps(value, false)))
        .expect("a static header and a body build")
}

/// `PlainTextResponse`, Starlette's own for an unknown route or method
/// and uvicorn's for a failure.
pub fn text(status: StatusCode, body: &str) -> Response<Body> {
    Response::builder()
        .status(status)
        .header(CONTENT_TYPE, "text/plain; charset=utf-8")
        .body(Body::from(body.to_string()))
        .expect("a static header and a body build")
}

/// The bearer gate's miss: a plain body, no envelope, the challenge.
pub fn unauthorized() -> Response<Body> {
    Response::builder()
        .status(StatusCode::UNAUTHORIZED)
        .header(WWW_AUTHENTICATE, "Bearer")
        .body(Body::from("unauthorized"))
        .expect("a static header and a body build")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::conversation::turn_records::size_refusal;

    #[test]
    fn a_turn_past_the_store_bound_is_413_and_reads_apart_from_a_long_body() {
        let bound = Fail::from(conversation::Error::Value(size_refusal(65_537, 65_536)));
        assert_eq!((bound.status.as_u16(), bound.code), (413, VALUE_ERROR));
        assert_eq!(
            bound.message,
            "resource limit NEOSIAN_MAX_RECORD_TURN_BYTES exceeded: 65537 > 65536; \
             this store does not keep a turn this large"
        );
        let part = Fail::oversize(PART_CEILING);
        assert_eq!((part.status.as_u16(), part.code), (413, VALUE_ERROR));
        assert_eq!(part.message, "request body exceeds 4194304 bytes");
        assert_eq!(
            Fail::oversize(CEILING).message,
            "request body exceeds 67108864 bytes"
        );
        assert!(!bound.message.starts_with("request body exceeds"));
    }

    #[test]
    fn every_other_value_refusal_stays_400() {
        for sentence in [
            "a part carries at most 2048 records",
            "resource limit NEOSIAN_MAX_TURN_BYTES exceeded: turn 2 of c holds 9 > 8 bytes",
            "the resource limit NEOSIAN_MAX_RECORD_TURN_BYTES exceeded: 2 > 1",
        ] {
            let fail = Fail::from(conversation::Error::Value(sentence.into()));
            assert_eq!((fail.status.as_u16(), fail.code), (400, VALUE_ERROR));
            assert_eq!(fail.message, sentence);
        }
        let store = Fail::from(error::Error::Value(size_refusal(2, 1)));
        assert_eq!(store.status.as_u16(), 400, "only a conversation refusal");
    }
}
