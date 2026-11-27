//! The per-session spool (`record/spool.py`, `record/locking.py`, DESIGN
//! §20.9, §39.3, §40.54 item 17.1): where an open span waits between the
//! prompt and the stop, one JSONL file per session under private modes,
//! never the store. At Stop the span is closed into the conversation's
//! queue (`landing/queue.rs`), where it is given its opening line, so the
//! next prompt starts a new span. A record is appended only once the
//! home's keep limit lets it (`landing/keep.rs`, item 17.7). The session
//! lock serializes a session's hook processes on it.

use std::fs::{self, File};
use std::io;
use std::path::{Path, PathBuf};

use serde_json::Value;

use super::payload::Record;
use crate::pyfmt::dumps_spaced;
use crate::store::fileio::{append_line, private_mkdir};
use crate::store::lock::acquire;
use crate::text::strip;

pub struct Spool {
    directory: PathBuf,
}

impl Spool {
    pub fn new(directory: &Path) -> Self {
        Self {
            directory: directory.to_path_buf(),
        }
    }

    /// A session id is one flat segment of the id grammar (§9.4), so it
    /// is a safe file name as is.
    pub fn file(&self, session_id: &str) -> PathBuf {
        self.directory.join(format!("{session_id}.jsonl"))
    }

    /// The session's lock, held for the landing; the file is the lock.
    pub fn lock(&self, session_id: &str) -> io::Result<File> {
        acquire(&self.directory.join(format!(".{session_id}.lock")))
    }

    #[cfg(test)]
    pub fn append(&self, session_id: &str, record: &Record) -> io::Result<()> {
        self.write(session_id, &line(record))
    }

    /// Appends one record's `line`, as `line` makes it, to the open span.
    /// The keep limit (`landing/keep.rs`) decides first whether it may.
    pub fn write(&self, session_id: &str, line: &str) -> io::Result<()> {
        private_mkdir(&self.directory)?;
        append_line(&self.file(session_id), line, false)
    }

    /// The open span's records by `parse`; the close reads the text itself.
    #[cfg(test)]
    pub fn read(&self, session_id: &str) -> io::Result<Vec<Record>> {
        let file = self.file(session_id);
        if !file.is_file() {
            return Ok(Vec::new());
        }
        parse(&fs::read_to_string(&file)?, &file)
    }

    pub fn clear(&self, session_id: &str) -> io::Result<()> {
        match fs::remove_file(self.file(session_id)) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            other => other,
        }
    }
}

/// A record as the spool keeps it: one line of spaced JSON and its newline.
pub fn line(record: &Record) -> String {
    let line = dumps_spaced(&Value::Object(record.clone()), false, false);
    format!("{line}\n")
}

/// The records of a span's text (an open span or a queued turn read from
/// `file`), one per newline-terminated line (a text's own line separators
/// never split one), a blank line skipped; a line that is not a JSON object
/// is refused naming the file and the line.
pub fn parse(text: &str, file: &Path) -> io::Result<Vec<Record>> {
    let mut records = Vec::new();
    each(text, file, |record| records.push(record))?;
    Ok(records)
}

/// `parse` one record at a time: each is handed to `take` and not kept, so
/// a reader that needs only a summary never holds every record at once.
pub fn each(text: &str, file: &Path, mut take: impl FnMut(Record)) -> io::Result<()> {
    for (index, line) in text.split('\n').enumerate() {
        if strip(line).is_empty() {
            continue;
        }
        let number = index + 1;
        let shown = file.display();
        let value: Value = serde_json::from_str(line).map_err(|error| {
            io::Error::other(format!("spool {shown} line {number} is not JSON: {error}"))
        })?;
        match value {
            Value::Object(record) => take(record),
            _ => {
                let why = format!("spool {shown} line {number} is not an object");
                return Err(io::Error::other(why));
            }
        }
    }
    Ok(())
}

/// A span's bytes as text, refused in the words `read_to_string` uses.
pub fn text_of(bytes: &[u8]) -> io::Result<&str> {
    std::str::from_utf8(bytes).map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "stream did not contain valid UTF-8",
        )
    })
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::testing::Scratch;

    #[test]
    fn a_span_waits_as_lines_and_a_record_is_never_split() {
        let scratch = Scratch::new("spool");
        let spool = Spool::new(&scratch.path().join("spool"));
        let prompt = json!({"kind": "prompt", "text": "one\u{2028}two é"});
        spool.append("s", prompt.as_object().unwrap()).unwrap();
        spool
            .append(
                "s",
                json!({"kind": "stop", "text": "done"}).as_object().unwrap(),
            )
            .unwrap();
        let text = fs::read_to_string(spool.file("s")).unwrap();
        assert_eq!(
            text,
            "{\"kind\": \"prompt\", \"text\": \"one\u{2028}two é\"}\n{\"kind\": \"stop\", \"text\": \"done\"}\n"
        );
        let records = spool.read("s").unwrap();
        assert_eq!(records.len(), 2);
        assert_eq!(records[0]["text"], "one\u{2028}two é");
        let _held = spool.lock("s").unwrap();
        assert!(spool.directory.join(".s.lock").is_file());
        spool.clear("s").unwrap();
        spool.clear("s").unwrap();
        assert!(spool.read("s").unwrap().is_empty());
        fs::write(spool.file("s"), "{\"kind\": \"prompt\"}\n\n7\n").unwrap();
        let refused = spool.read("s").unwrap_err().to_string();
        assert!(refused.ends_with("line 3 is not an object"), "{refused}");
        fs::write(spool.file("s"), "{\n").unwrap();
        assert!(
            spool
                .read("s")
                .unwrap_err()
                .to_string()
                .contains("line 1 is not JSON: ")
        );
    }
}
