//! The per-session spool (`record/spool.py`, `record/locking.py`, DESIGN
//! §20.9, §39.3): where an open span waits between the prompt and the
//! stop, one JSONL file per session under private modes, never the store.
//! The record lands at Stop, whole, and the file goes; a failed landing
//! keeps it, so the next Stop carries the whole span. The session lock
//! serializes a session's hook processes on it.

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

    pub fn append(&self, session_id: &str, record: &Record) -> io::Result<()> {
        private_mkdir(&self.directory)?;
        let line = dumps_spaced(&Value::Object(record.clone()), false, false);
        append_line(&self.file(session_id), &format!("{line}\n"), false)
    }

    /// Every record, one per newline-terminated line (a text's own line
    /// separators never split one), a blank line skipped; a line that is
    /// not a JSON object is refused naming the file and the line.
    pub fn read(&self, session_id: &str) -> io::Result<Vec<Record>> {
        let file = self.file(session_id);
        if !file.is_file() {
            return Ok(Vec::new());
        }
        let text = fs::read_to_string(&file)?;
        let mut records = Vec::new();
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
                Value::Object(record) => records.push(record),
                _ => {
                    let why = format!("spool {shown} line {number} is not an object");
                    return Err(io::Error::other(why));
                }
            }
        }
        Ok(records)
    }

    pub fn clear(&self, session_id: &str) -> io::Result<()> {
        match fs::remove_file(self.file(session_id)) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            other => other,
        }
    }
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
