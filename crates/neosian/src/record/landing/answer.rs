//! One answer to a request of the lander, as the rules of 17.4 read it
//! (DESIGN §40.54 item 17.4, rulings D8 and D10 of the T1e1 plan). A
//! server's `Failure` keeps what it came with; a local store's result is
//! mapped as `neosian serve` answers the same store error
//! (`serve/envelope.rs`): a refusal at 400 with its code and details, a
//! bare sentence as `value_error`, at 413 when it is the store's bound on a
//! turn (item 8.2). So `rules.rs` reads a local store and a server alike. A
//! local failure to read or write a file is no answer: it stops the run.

use serde_json::json;

use crate::conversation::Error;
use crate::conversation::turn_records::Written;
use crate::limits::{RECORD_TURN_BYTES, RESPONSE};
use crate::remote::records::{Failure, Refusal};
use crate::store::error::Error as StoreError;

/// The code of a refused value: a record, a part or the turn.
pub const VALUE_ERROR: &str = "value_error";

/// What a request was answered.
#[derive(Debug)]
pub enum Answer {
    /// The store took a `write_records` request: the draft's state,
    /// `next_seq` and, once sealed, the turn's header.
    Written(Written),
    /// The store refused it; nothing of the request was stored.
    Refused(Refusal),
    /// No answer arrived, or it broke off: the request may have been
    /// applied.
    Lost(String),
    /// A 2xx that is not the route's answer, or one past this client's
    /// response budget: the request was applied.
    Unreadable(String),
    /// This side could not ask: `credential` when the credential cannot
    /// be read (a managed login signed out or reassigned), else a setting.
    Unasked { credential: bool, message: String },
    /// The handshake declared no turn records version 1; nothing was sent.
    Unsupported,
}

/// A local store's answer to `write_records`.
pub fn local(result: Result<Written, Error>) -> Result<Answer, Error> {
    match result {
        Ok(written) => Ok(Answer::Written(written)),
        Err(error) => refused(error).map(Answer::Refused),
    }
}

/// A local store's refusal as `neosian serve` would send it, or the I/O
/// error that is no answer.
pub fn refused(error: Error) -> Result<Refusal, Error> {
    let (status, code, message, details) = match error {
        Error::Conversation(refused) => (400, refused.code, refused.message, refused.details),
        Error::Memory(error) => (400, error.code, error.message, error.details),
        Error::Value(sentence) => {
            let bound = format!("resource limit {RECORD_TURN_BYTES} exceeded: ");
            let status = if sentence.starts_with(&bound) {
                413
            } else {
                400
            };
            (status, VALUE_ERROR, sentence, json!({}))
        }
        error @ Error::Io(_) => return Err(error),
    };
    Ok(Refusal {
        status,
        retry_after: None,
        code: Some(code.to_string()),
        message,
        details,
    })
}

/// A server's failure as it came. A 2xx this client's response budget
/// refused was applied, as an unreadable one was; a credential that cannot
/// be read is told from a setting out of range by its kind (the client
/// raises the first as a configuration error, the second as a value).
pub fn remote(failure: Failure) -> Answer {
    let text = |error: StoreError| Error::from(error).text();
    match failure {
        Failure::Unsupported => Answer::Unsupported,
        Failure::Refused(refusal) => Answer::Refused(*refusal),
        Failure::Transport(error) => Answer::Lost(text(error)),
        Failure::Unreadable(what) => Answer::Unreadable(format!("malformed wire response: {what}")),
        Failure::Client(StoreError::Value(message))
            if message.starts_with(&format!("resource limit {RESPONSE} exceeded: ")) =>
        {
            Answer::Unreadable(message)
        }
        Failure::Client(error @ StoreError::Memory(_)) => Answer::Unasked {
            credential: true,
            message: text(error),
        },
        Failure::Client(error) => Answer::Unasked {
            credential: false,
            message: text(error),
        },
    }
}

/// What the person is told of an answer that keeps the turn: a refusal's
/// `[code] message`, or what happened instead.
pub fn said(answer: &Answer) -> String {
    match answer {
        Answer::Written(written) => format!(
            "draft {} answered {} to a part",
            written.draft,
            written.state.as_str()
        ),
        Answer::Refused(refusal) => match &refusal.code {
            Some(code) => format!("[{code}] {}", refusal.message),
            None if refusal.message.is_empty() => {
                format!("the destination answered {}", refusal.status)
            }
            None => format!(
                "the destination answered {}: {}",
                refusal.status, refusal.message
            ),
        },
        Answer::Lost(message) => format!("no answer from the destination: {message}"),
        Answer::Unreadable(message) | Answer::Unasked { message, .. } => message.clone(),
        Answer::Unsupported => "the destination does not keep turn records".to_string(),
    }
}
