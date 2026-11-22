//! A client file read as Claude Code 2.1.330 reads it (DESIGN §40.51
//! amendment, R1; record/x3a-real-cli-2026-11-21/fix-round): a UTF-8
//! byte-order mark is skipped, invalid UTF-8 is decoded lossily, a duplicate
//! JSON key keeps its last value, and a file with comments or trailing commas
//! is not read at all, except OpenCode's `.jsonc` (`Read::jsonc`, §40.53).
//! Whether neosian may edit it is a separate question.

mod jsonc;

pub use jsonc::{blank, comments};

use crate::clients::files;
use serde_json::Value;
use std::{fs, path::Path};

const BOM: &[u8] = b"\xef\xbb\xbf";

pub struct Read {
    /// The text without a byte-order mark, invalid bytes replaced.
    pub text: String,
    pub bom: bool,
    /// The bytes are valid UTF-8, so an edit keeps every other byte.
    pub strict: bool,
    /// The owner can write the file (and its directory).
    pub writable: bool,
    raw: Vec<u8>,
}

/// The file's reading; `None` when it is absent or cannot be read at all.
pub fn file(path: &Path) -> Option<Read> {
    let raw = fs::read(path).ok()?;
    let (body, bom) = match raw.strip_prefix(BOM) {
        Some(body) => (body, true),
        None => (&raw[..], false),
    };
    Some(Read {
        text: String::from_utf8_lossy(body).into_owned(),
        bom,
        strict: std::str::from_utf8(body).is_ok(),
        writable: files::unwritable(path).is_none(),
        raw,
    })
}

impl Read {
    /// The JSON object with comments and trailing commas allowed, as
    /// OpenCode reads a `.jsonc`.
    pub fn jsonc(&self) -> Option<Value> {
        self.json().or_else(|| {
            serde_json::from_str(&blank(&self.text))
                .ok()
                .filter(Value::is_object)
        })
    }

    /// The JSON object as Claude reads it (the last of duplicate keys).
    pub fn json(&self) -> Option<Value> {
        serde_json::from_str(&self.text)
            .ok()
            .filter(Value::is_object)
    }

    /// The exact text on disk, for the write guard (only when strict).
    pub fn original(&self) -> Option<String> {
        String::from_utf8(self.raw.clone()).ok()
    }

    /// An edited text with the file's byte-order mark kept.
    pub fn with_bom(&self, text: &str) -> String {
        if self.bom {
            format!("\u{feff}{text}")
        } else {
            text.to_owned()
        }
    }

    /// Why an edit cannot be written: an asset key, `None` when it can.
    pub fn blocked(&self) -> Option<&'static str> {
        if !self.strict {
            Some("not_utf8")
        } else if !self.writable {
            Some("unwritable")
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_bom_invalid_bytes_and_duplicate_keys_read_as_claude_reads_them() {
        let scratch = crate::testing::Scratch::new("rename-read");
        let path = scratch.path().join("s.json");
        fs::write(
            &path,
            b"\xef\xbb\xbf{\"a\": \"Jos\xe9\", \"p\": 1, \"p\": 2}",
        )
        .unwrap();
        let read = file(&path).unwrap();
        assert!(read.bom && !read.strict && read.writable);
        let value = read.json().unwrap();
        assert_eq!(value["p"], 2);
        assert_eq!(value["a"], "Jos\u{fffd}");
        assert_eq!(read.blocked(), Some("not_utf8"));
        fs::write(&path, "{\n // c\n \"neosian\": 1}").unwrap();
        let read = file(&path).unwrap();
        assert!(read.json().is_none());
        assert_eq!(read.with_bom("x"), "x");
    }
}
