//! Spooled answers (DESIGN §40.46, amended 2026-11-17, #417, #419): a route
//! writes its answer item by item into a spool while it holds the store (the
//! file store's lock, a Postgres snapshot or connection), in memory up to
//! `MEMORY` and then, the whole answer, in a private temporary file charged
//! against `NEOSIAN_MAX_TEMP_BYTES`. The store is released when the route
//! returns, and only then is the answer sent, with its length. Every refusal
//! is known before the status line and answered as before; a slow reader
//! holds a file descriptor and disk, never the store. The bytes are exactly
//! the whole answer's.
//!
//! When no spool file can be made or written (an unwritable or full
//! temporary directory), the answer is finished in memory, as 2.16.0 held
//! it, within `NEOSIAN_MAX_RESPONSE_BYTES`; past that it is refused with
//! `value_error` naming that setting, which a size-paged retry answers. A
//! spool failing midway is read back into memory first, so the answer is
//! never cut and never a bare 500.

use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom, Write};

use axum::body::{Body, Bytes};
use axum::http::StatusCode;
use axum::http::header::{CONTENT_LENGTH, CONTENT_TYPE};
use axum::response::Response;
use serde_json::Value;

use super::envelope::Fail;
use crate::limits::{self, Scratch};
use crate::pyfmt::dumps;

/// The largest piece written to or read back from a spool file at once.
pub const CHUNK: usize = 64 * 1024;
/// How much of an answer stays in memory before it spills to disk.
pub const MEMORY: usize = 1 << 20;

/// Where a spooled route writes its answer.
pub struct Writer {
    held: Vec<u8>,
    file: Option<Spilled>,
    /// Set once no spool can be made or written: the answer is held in
    /// memory within this many bytes.
    memory: Option<u64>,
    /// Tests: spool writes fail once this many bytes are on disk.
    #[cfg(test)]
    fail_after: Option<u64>,
}

/// The part of an answer on disk: the private staging area (removed when
/// dropped) and its one file, `written` bytes known on disk and the rest
/// still `pending` in memory.
struct Spilled {
    scratch: Scratch,
    file: File,
    pending: Vec<u8>,
    written: u64,
    maximum: u64,
    #[cfg(test)]
    fail_after: Option<u64>,
}

/// Why a spool could not take bytes: a refusal (its budget) is the
/// caller's; anything else means the answer goes on in memory.
enum Spill {
    Refused(io::Error),
    Unavailable,
}

impl Writer {
    fn new() -> Self {
        Self {
            held: Vec::new(),
            file: None,
            memory: None,
            #[cfg(test)]
            fail_after: None,
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
        if let Some(spilled) = self.file.as_mut() {
            return match spilled.write(bytes) {
                Ok(()) => Ok(()),
                Err(Spill::Refused(error)) => Err(error),
                // The bytes are in `pending`, so they come back with the rest.
                Err(Spill::Unavailable) => self.in_memory(),
            };
        }
        if self.memory.is_none() && self.held.len() + bytes.len() <= MEMORY {
            self.held.extend_from_slice(bytes);
            return Ok(());
        }
        if self.memory.is_none() {
            match self.spill() {
                Ok(spilled) => {
                    self.held = Vec::new();
                    self.file = Some(spilled);
                    return self.bytes(bytes);
                }
                Err(Spill::Refused(error)) => return Err(error),
                Err(Spill::Unavailable) => self.memory = Some(response_bytes()?),
            }
        }
        let maximum = self.memory.expect("held in memory");
        let used = self.held.len() as u64 + bytes.len() as u64;
        limits::check(limits::RESPONSE, used, maximum).map_err(limits::into_io)?;
        self.held.extend_from_slice(bytes);
        Ok(())
    }

    fn spill(&self) -> Result<Spilled, Spill> {
        let mut spilled = Spilled::open()?;
        #[cfg(test)]
        {
            spilled.fail_after = self.fail_after;
        }
        spilled.write(&self.held)?;
        Ok(spilled)
    }

    /// The spool failed: what it holds comes back into memory, checked
    /// against the response budget before it is read.
    fn in_memory(&mut self) -> io::Result<()> {
        let spilled = self.file.take().expect("a spool to read back");
        let maximum = response_bytes()?;
        self.held = spilled.recover(maximum)?;
        self.memory = Some(maximum);
        Ok(())
    }

    /// The answer, sent from memory or from its file, with its length.
    fn response(mut self) -> io::Result<Response> {
        if let Some(spilled) = self.file.as_mut()
            && let Err(Spill::Unavailable) = spilled.flush()
        {
            self.in_memory()?;
        }
        let builder = Response::builder()
            .status(StatusCode::OK)
            .header(CONTENT_TYPE, "application/json");
        let Some(spilled) = self.file else {
            return Ok(builder
                .body(Body::from(self.held))
                .expect("a static header and a body build"));
        };
        let length = spilled.written;
        let scratch = spilled.scratch;
        let mut file = spilled.file;
        file.seek(SeekFrom::Start(0))?;
        let body = futures_util::stream::unfold(Some((file, scratch, length)), read_back);
        Ok(builder
            .header(CONTENT_LENGTH, length)
            .body(Body::from_stream(body))
            .expect("a static header and a body build"))
    }
}

fn response_bytes() -> io::Result<u64> {
    limits::response_bytes().map_err(limits::into_io)
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
    fn open() -> Result<Self, Spill> {
        let maximum =
            limits::temp_bytes().map_err(|error| Spill::Refused(limits::into_io(error)))?;
        let root = super::staging::root().map_err(|_| Spill::Unavailable)?;
        let scratch = Scratch::beside(&root).map_err(|error| match error {
            crate::store::error::Error::Io(_) => Spill::Unavailable,
            other => Spill::Refused(limits::into_io(other)),
        })?;
        let file = crate::store::fileio::private()
            .read(true)
            .write(true)
            .create_new(true)
            .open(scratch.directory.join("answer.json"))
            .map_err(|_| Spill::Unavailable)?;
        Ok(Self {
            scratch,
            file,
            pending: Vec::new(),
            written: 0,
            maximum,
            #[cfg(test)]
            fail_after: None,
        })
    }

    /// The whole answer so far is charged against the temporary budget.
    fn write(&mut self, bytes: &[u8]) -> Result<(), Spill> {
        let used = self.written + self.pending.len() as u64 + bytes.len() as u64;
        limits::check(limits::TEMP, used, self.maximum)
            .map_err(|error| Spill::Refused(limits::into_io(error)))?;
        self.pending.extend_from_slice(bytes);
        if self.pending.len() >= CHUNK {
            self.flush()?;
        }
        Ok(())
    }

    fn flush(&mut self) -> Result<(), Spill> {
        #[cfg(test)]
        if self
            .fail_after
            .is_some_and(|after| self.written + self.pending.len() as u64 > after)
        {
            return Err(Spill::Unavailable);
        }
        self.file
            .write_all(&self.pending)
            .map_err(|_| Spill::Unavailable)?;
        self.written += self.pending.len() as u64;
        self.pending.clear();
        Ok(())
    }

    /// What the spool holds, back in memory: the bytes known on disk read
    /// back, then those still pending. The staging area goes with it.
    fn recover(mut self, maximum: u64) -> io::Result<Vec<u8>> {
        let total = self.written + self.pending.len() as u64;
        limits::check(limits::RESPONSE, total, maximum).map_err(limits::into_io)?;
        let mut held = Vec::with_capacity(total as usize);
        self.file.seek(SeekFrom::Start(0))?;
        (&self.file).take(self.written).read_to_end(&mut held)?;
        if held.len() as u64 != self.written {
            return Err(io::Error::other("spool read back short"));
        }
        held.append(&mut self.pending);
        Ok(held)
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

    /// Drive a writer directly: `between` runs once the answer has spilled.
    fn written(
        items: &[Value],
        fail_after: Option<u64>,
        between: impl FnOnce(&Writer),
    ) -> (Option<String>, Vec<u8>) {
        let mut writer = Writer::new();
        writer.fail_after = fail_after;
        let mut between = Some(between);
        writer.text("{\"turns\":[").unwrap();
        for (index, item) in items.iter().enumerate() {
            if index > 0 {
                writer.text(",").unwrap();
            }
            writer.value(item).unwrap();
            if writer.file.is_some()
                && let Some(between) = between.take()
            {
                between(&writer);
            }
        }
        writer.text("],\"next_after\":null}").unwrap();
        let response = writer.response().unwrap();
        let length = response
            .headers()
            .get(CONTENT_LENGTH)
            .map(|value| value.to_str().unwrap().to_string());
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        let body = runtime
            .block_on(axum::body::to_bytes(response.into_body(), usize::MAX))
            .unwrap();
        (length, body.to_vec())
    }

    #[test]
    fn a_spool_that_fails_midway_finishes_the_answer_in_memory() {
        // #419: a full disk (here, writes failing past 1.5 MiB on disk) or a
        // vanished directory never cuts the answer or turns it into a 500.
        let items: Vec<Value> = (0..300)
            .map(|i| json!({"n": i, "text": "é\n".repeat(4000 + i)}))
            .collect();
        let whole = dumps(&json!({"turns": items, "next_after": null}), false);
        assert!(whole.len() > 3 * MEMORY);
        let (length, body) = written(&items, Some(3 * MEMORY as u64 / 2), |_| {});
        assert_eq!(length, None, "answered from memory");
        assert_eq!(String::from_utf8(body).unwrap(), whole);
        // A directory removed under an open file (Unix; Windows refuses).
        #[cfg(unix)]
        {
            let (length, body) = written(&items, None, |writer| {
                let spilled = writer.file.as_ref().unwrap();
                std::fs::remove_dir_all(&spilled.scratch.directory).unwrap();
            });
            assert!(length.is_some());
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
