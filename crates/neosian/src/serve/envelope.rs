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
use crate::pyfmt::dumps;
use crate::repr::repr;
use crate::store::error::{self, MemoryError};

pub const VALUE_ERROR: &str = "value_error";
pub const FORBIDDEN: &str = "forbidden";
/// `MAX_REQUEST_BYTES`: one ceiling over every surface (IN-3).
pub const CEILING: usize = 64 * 1024 * 1024;

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

    /// A body over the ceiling, in the same envelope.
    pub fn oversize() -> Self {
        Self::enveloped(
            StatusCode::PAYLOAD_TOO_LARGE,
            VALUE_ERROR,
            format!("request body exceeds {CEILING} bytes"),
            json!({}),
        )
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
            conversation::Error::Value(sentence) => Self::value(sentence),
            conversation::Error::Io(_) => Self::internal(),
        }
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
