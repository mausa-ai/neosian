//! Spooled answers (DESIGN §40.46, amended 2026-11-17, #417): a route writes
//! its answer item by item into a spool while it holds the store (the file
//! store's lock, a Postgres snapshot or connection), in memory up to
//! `MEMORY` and then in a private temporary file charged against
//! `NEOSIAN_MAX_TEMP_BYTES`. The store is released when the route returns,
//! and only then is the answer sent, with its length. Every refusal is
//! known before the status line and answered as before; a slow reader holds
//! a file descriptor and disk, never the store; the server holds about one
//! item plus fixed buffers. The bytes are exactly the whole answer's.

use std::fs::File;
use std::io::{self, BufWriter, Read, Seek, SeekFrom, Write};

use axum::body::{Body, Bytes};
use axum::http::StatusCode;
use axum::http::header::{CONTENT_LENGTH, CONTENT_TYPE};
use axum::response::Response;
use serde_json::Value;

use super::envelope::Fail;
use crate::limits::{self, Scratch};
use crate::pyfmt::dumps;

/// The largest piece read back from a spool file at once.
pub const CHUNK: usize = 64 * 1024;
/// How much of an answer stays in memory before it spills to disk.
pub const MEMORY: usize = 1 << 20;

/// Where a spooled route writes its answer.
pub struct Writer {
    held: Vec<u8>,
    file: Option<Spilled>,
}

/// The part of an answer on disk: the private staging area (removed when
/// dropped), its one file and how many bytes it holds.
struct Spilled {
    scratch: Scratch,
    out: BufWriter<File>,
    bytes: u64,
    maximum: u64,
}

impl Writer {
    fn new() -> Self {
        Self {
            held: Vec::new(),
            file: None,
        }
    }

    /// Literal answer text: keys, separators and small values.
    pub fn text(&mut self, text: &str) -> io::Result<()> {
        self.bytes(text.as_bytes())
    }

    /// One JSON value as `dumps` writes it.
    pub fn value(&mut self, value: &Value) -> io::Result<()> {
        self.encoded(dumps(value, false))
    }

    /// An already encoded item.
    pub fn encoded(&mut self, item: String) -> io::Result<()> {
        self.bytes(item.as_bytes())
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

    fn bytes(&mut self, bytes: &[u8]) -> io::Result<()> {
        if self.file.is_none() && self.held.len() + bytes.len() <= MEMORY {
            self.held.extend_from_slice(bytes);
            return Ok(());
        }
        let spilled = match self.file.as_mut() {
            Some(spilled) => spilled,
            None => {
                let held = std::mem::take(&mut self.held);
                self.file.insert(Spilled::open(&held)?)
            }
        };
        spilled.write(bytes)
    }

    /// The answer, sent from memory or from its file, with its length.
    fn response(self) -> io::Result<Response> {
        let builder = Response::builder()
            .status(StatusCode::OK)
            .header(CONTENT_TYPE, "application/json");
        let Some(spilled) = self.file else {
            return Ok(builder
                .body(Body::from(self.held))
                .expect("a static header and a body build"));
        };
        let length = spilled.bytes;
        let scratch = spilled.scratch;
        let mut file = spilled
            .out
            .into_inner()
            .map_err(io::IntoInnerError::into_error)?;
        file.seek(SeekFrom::Start(0))?;
        let body = futures_util::stream::unfold(Some((file, scratch, length)), read_back);
        Ok(builder
            .header(CONTENT_LENGTH, length)
            .body(Body::from_stream(body))
            .expect("a static header and a body build"))
    }
}

type Back = Option<(File, Scratch, u64)>;

/// The next piece of a spool file, read off the runtime's workers; the
/// staging area goes with the last piece. A spool that cannot be read back
/// ends the body short, so the connection closes, as any I/O failure does.
async fn read_back(state: Back) -> Option<(io::Result<Bytes>, Back)> {
    let (mut file, scratch, left) = state?;
    if left == 0 {
        return None;
    }
    let read = tokio::task::spawn_blocking(move || {
        let mut chunk = vec![0; CHUNK.min(left as usize)];
        let outcome = file.read_exact(&mut chunk).map(|()| chunk);
        (file, outcome)
    })
    .await;
    match read {
        Ok((file, Ok(chunk))) => {
            let left = left - chunk.len() as u64;
            Some((Ok(Bytes::from(chunk)), Some((file, scratch, left))))
        }
        _ => Some((Err(io::Error::other("spool read failed")), None)),
    }
}

impl Spilled {
    fn open(held: &[u8]) -> io::Result<Self> {
        let maximum = limits::temp_bytes().map_err(limits::into_io)?;
        let scratch = Scratch::new().map_err(limits::into_io)?;
        let file = crate::store::fileio::private()
            .read(true)
            .write(true)
            .create_new(true)
            .open(scratch.directory.join("answer.json"))?;
        let mut spilled = Self {
            scratch,
            out: BufWriter::with_capacity(CHUNK, file),
            bytes: 0,
            maximum,
        };
        spilled.write(held)?;
        Ok(spilled)
    }

    fn write(&mut self, bytes: &[u8]) -> io::Result<()> {
        self.bytes += bytes.len() as u64;
        limits::check(limits::TEMP, self.bytes, self.maximum).map_err(limits::into_io)?;
        self.out.write_all(bytes)
    }
}

/// A route body writing into a `Writer`.
pub type Produce = Box<dyn FnOnce(&mut Writer) -> Result<(), Fail> + Send>;

/// Run `produce` on the blocking pool, holding the store only while it
/// runs, then answer with what it wrote.
pub async fn respond(produce: Produce) -> Response {
    let made = tokio::task::spawn_blocking(move || {
        let mut writer = Writer::new();
        produce(&mut writer)?;
        // The route has returned, so the store is released; the spool is not.
        writer.response().map_err(Fail::from)
    })
    .await;
    match made {
        Ok(Ok(response)) => response,
        Ok(Err(fail)) => fail.response(),
        Err(_) => Fail::internal().response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn collect(produce: Produce) -> (StatusCode, Option<String>, Vec<u8>) {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .unwrap();
        runtime.block_on(async {
            let response = respond(produce).await;
            let status = response.status();
            let length = response
                .headers()
                .get(CONTENT_LENGTH)
                .map(|value| value.to_str().unwrap().to_string());
            let body = axum::body::to_bytes(response.into_body(), usize::MAX).await;
            (status, length, body.map(|b| b.to_vec()).unwrap_or_default())
        })
    }

    fn answer(items: Vec<Value>) -> Produce {
        Box::new(move |writer| {
            writer.text("{\"turns\":[")?;
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    writer.text(",")?;
                }
                writer.value(item)?;
            }
            Ok(writer.text("],\"next_after\":null}")?)
        })
    }

    #[test]
    fn a_spooled_answer_is_the_whole_answer() {
        for count in [3, 400] {
            let items: Vec<Value> = (0..count)
                .map(|i| json!({"n": i, "text": "é\n".repeat(i * 9)}))
                .collect();
            let whole = dumps(&json!({"turns": items, "next_after": null}), false);
            let (status, length, body) = collect(answer(items));
            assert_eq!(status, StatusCode::OK);
            if whole.len() > MEMORY {
                // From disk, with its length set explicitly.
                assert_eq!(length, Some(whole.len().to_string()));
            }
            assert_eq!(String::from_utf8(body).unwrap(), whole);
        }
    }

    #[test]
    fn a_refusal_is_an_ordinary_response_wherever_it_is_found() {
        let (status, _, body) = collect(Box::new(|writer| {
            writer.text("{\"turns\":[")?;
            writer.encoded(format!("\"{}\"", "x".repeat(2 * MEMORY)))?;
            Err(Fail::value("found after two MiB"))
        }));
        assert_eq!(status, StatusCode::BAD_REQUEST);
        let refused: Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(refused["error"]["message"], "found after two MiB");
    }
}
