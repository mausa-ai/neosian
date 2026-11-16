//! Streamed answers (DESIGN §40.46): a route writes its answer item by item
//! from the blocking pool into a bounded channel the response body drains,
//! so the server holds about one item, never the whole answer. The bytes are
//! exactly the whole answer's. The start of an answer is held until 64 KiB
//! are ready or it is complete, so an early refusal is an ordinary response;
//! a refusal after bytes have left is a trailer: a line feed, then the error
//! envelope as one compact JSON object. A compact answer never holds a raw
//! line feed, so the body is then no longer JSON and cannot pass for a
//! shorter answer. An infrastructure failure aborts the body instead.

use std::io;
use std::sync::mpsc::{Receiver, SyncSender, TrySendError, sync_channel};
use std::time::{Duration, Instant};

use axum::body::{Body, Bytes};
use axum::http::StatusCode;
use axum::http::header::CONTENT_TYPE;
use axum::response::Response;
use serde_json::{Value, json};

use super::envelope::Fail;
use crate::pyfmt::dumps;

/// The largest chunk sent at once, and how much of an answer is held
/// before its first byte leaves.
pub const CHUNK: usize = 64 * 1024;
/// Chunks waiting for the socket: a fixed buffer beside the item in hand.
const QUEUE: usize = 4;
/// A client that stops reading this long loses the answer, so a stalled
/// reader cannot hold a store lock or a database connection.
const STALL: Duration = Duration::from_secs(30);

enum Message {
    Data(Bytes),
    Refused(Fail),
    End,
}

/// Where a streamed route writes its answer.
pub struct Writer {
    sender: SyncSender<Message>,
    held: Vec<u8>,
    started: bool,
}

impl Writer {
    /// Literal answer text: keys, separators and small values.
    pub fn text(&mut self, text: &str) -> io::Result<()> {
        self.held.extend_from_slice(text.as_bytes());
        if self.held.len() >= CHUNK {
            self.flush()?;
        }
        Ok(())
    }

    /// One JSON value as `dumps` writes it.
    pub fn value(&mut self, value: &Value) -> io::Result<()> {
        self.encoded(dumps(value, false))
    }

    /// An already encoded item; a large one leaves in slices of its own buffer.
    pub fn encoded(&mut self, item: String) -> io::Result<()> {
        if self.held.len() + item.len() < CHUNK {
            return self.text(&item);
        }
        self.flush()?;
        let bytes = Bytes::from(item);
        let mut start = 0;
        while start < bytes.len() {
            let end = (start + CHUNK).min(bytes.len());
            self.started = true;
            self.send(Message::Data(bytes.slice(start..end)))?;
            start = end;
        }
        Ok(())
    }

    /// `{"<key>":[items…],"<name>":value…}`, one item encoded at a time.
    pub fn list(
        &mut self,
        key: &str,
        items: impl IntoIterator<Item = Value>,
        rest: &[(&str, Value)],
    ) -> io::Result<()> {
        self.text(&format!("{{\"{key}\":["))?;
        for (index, item) in items.into_iter().enumerate() {
            if index > 0 {
                self.text(",")?;
            }
            self.value(&item)?;
        }
        self.text("]")?;
        for (name, value) in rest {
            self.text(&format!(",\"{name}\":"))?;
            self.value(value)?;
        }
        self.text("}")
    }

    fn flush(&mut self) -> io::Result<()> {
        if self.held.is_empty() {
            return Ok(());
        }
        let bytes = Bytes::from(std::mem::take(&mut self.held));
        self.started = true;
        self.send(Message::Data(bytes))
    }

    fn send(&self, message: Message) -> io::Result<()> {
        let deadline = Instant::now() + STALL;
        let mut pause = Duration::from_micros(20);
        let mut message = message;
        loop {
            match self.sender.try_send(message) {
                Ok(()) => return Ok(()),
                Err(TrySendError::Disconnected(_)) => {
                    return Err(io::Error::new(io::ErrorKind::BrokenPipe, "client gone"));
                }
                Err(TrySendError::Full(back)) => {
                    if Instant::now() >= deadline {
                        return Err(io::Error::new(io::ErrorKind::TimedOut, "client stalled"));
                    }
                    message = back;
                    std::thread::sleep(pause);
                    pause = (pause * 2).min(Duration::from_millis(1));
                }
            }
        }
    }

    /// The route's outcome: the rest of the answer, the ordinary refusal when
    /// nothing has left yet, else the trailer (or an abort for a failure that
    /// has no envelope).
    fn finish(mut self, outcome: Result<(), Fail>) {
        let sent = match outcome {
            Ok(()) => self.flush().and_then(|()| self.send(Message::End)),
            Err(fail) if !self.started => self.send(Message::Refused(fail)),
            Err(fail) if fail.status == StatusCode::INTERNAL_SERVER_ERROR => Ok(()),
            Err(fail) => {
                let envelope = json!({
                    "error": {"code": fail.code, "message": fail.message, "details": *fail.details}
                });
                self.held.push(b'\n');
                self.held
                    .extend_from_slice(dumps(&envelope, false).as_bytes());
                self.flush().and_then(|()| self.send(Message::End))
            }
        };
        // A client that went away or stalled has nothing left to receive.
        let _ = sent;
    }
}

/// A route body writing into a `Writer`.
pub type Produce = Box<dyn FnOnce(&mut Writer) -> Result<(), Fail> + Send>;

/// Run `produce` on the blocking pool and answer with what it streams.
pub async fn respond(produce: Produce) -> Response {
    let (sender, receiver) = sync_channel(QUEUE);
    tokio::task::spawn_blocking(move || {
        let mut writer = Writer {
            sender,
            held: Vec::new(),
            started: false,
        };
        let outcome = produce(&mut writer);
        writer.finish(outcome);
    });
    let (first, receiver) = next(receiver).await;
    match first {
        Some(Message::Refused(fail)) => fail.response(),
        Some(Message::Data(bytes)) => {
            let rest = futures_util::stream::unfold(Some(receiver), |state| async move {
                let receiver = state?;
                match next(receiver).await {
                    (Some(Message::Data(bytes)), receiver) => {
                        Some((Ok::<Bytes, io::Error>(bytes), Some(receiver)))
                    }
                    (Some(Message::End), _) => None,
                    // The producer stopped without finishing: abort the body.
                    _ => Some((Err(io::Error::other("answer aborted")), None)),
                }
            });
            let first = futures_util::stream::once(async move { Ok(bytes) });
            Response::builder()
                .status(StatusCode::OK)
                .header(CONTENT_TYPE, "application/json")
                .body(Body::from_stream(futures_util::StreamExt::chain(
                    first, rest,
                )))
                .expect("a static header and a body build")
        }
        _ => Fail::internal().response(),
    }
}

/// The next message, received off the runtime's workers.
async fn next(receiver: Receiver<Message>) -> (Option<Message>, Receiver<Message>) {
    tokio::task::spawn_blocking(move || {
        let message = receiver.recv().ok();
        (message, receiver)
    })
    .await
    .expect("a receive never panics")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn collect(produce: Produce) -> (StatusCode, Vec<u8>) {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .unwrap();
        runtime.block_on(async {
            let response = respond(produce).await;
            let status = response.status();
            let body = axum::body::to_bytes(response.into_body(), usize::MAX).await;
            (status, body.map(|b| b.to_vec()).unwrap_or_default())
        })
    }

    #[test]
    fn a_streamed_answer_is_the_whole_answer_byte_for_byte() {
        let items: Vec<Value> = (0..40)
            .map(|i| json!({"n": i, "text": "é\n".repeat(i * 900)}))
            .collect();
        let whole = dumps(&json!({"turns": items, "next_after": null}), false);
        let (status, body) = collect(Box::new(move |writer| {
            writer.text("{\"turns\":[")?;
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    writer.text(",")?;
                }
                writer.value(item)?;
            }
            Ok(writer.text("],\"next_after\":null}")?)
        }));
        assert_eq!(status, StatusCode::OK);
        assert_eq!(String::from_utf8(body).unwrap(), whole);
        assert!(
            !whole.contains('\n'),
            "a compact answer holds no raw line feed"
        );
    }

    #[test]
    fn an_early_refusal_is_a_response_and_a_late_one_a_trailer() {
        let (status, body) = collect(Box::new(|writer| {
            writer.text("{\"turns\":[")?;
            Err(Fail::value("too early"))
        }));
        assert_eq!(status, StatusCode::BAD_REQUEST);
        let refused: Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(refused["error"]["message"], "too early");
        let (status, body) = collect(Box::new(|writer| {
            writer.text("{\"turns\":[")?;
            writer.encoded(format!("\"{}\"", "x".repeat(CHUNK)))?;
            Err(Fail::value(
                "resource limit NEOSIAN_MAX_RESPONSE_BYTES exceeded",
            ))
        }));
        assert_eq!(status, StatusCode::OK);
        assert!(serde_json::from_slice::<Value>(&body).is_err());
        let at = body.iter().position(|b| *b == b'\n').unwrap();
        let trailer: Value = serde_json::from_slice(&body[at + 1..]).unwrap();
        assert_eq!(trailer["error"]["code"], "value_error");
        assert_eq!(
            trailer["error"]["message"],
            "resource limit NEOSIAN_MAX_RESPONSE_BYTES exceeded"
        );
    }
}
