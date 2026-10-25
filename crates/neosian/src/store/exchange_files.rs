//! On-disk artifacts with a streaming, format-1-compatible replica fingerprint.

use super::error::Error;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

#[derive(Clone)]
pub(super) struct Artifact {
    pub file: PathBuf,
    pub bytes: u64,
    digest: String,
}
pub(super) type Files = BTreeMap<String, Artifact>;

impl Artifact {
    pub fn open(file: PathBuf) -> Result<Self, Error> {
        let bytes = fs::metadata(&file)?.len();
        crate::limits::check(crate::limits::FILE, bytes, crate::limits::file_bytes()?)?;
        let mut hash = Hash(Sha256::new());
        io::copy(&mut File::open(&file)?, &mut hash)?;
        Ok(Self {
            file,
            bytes,
            digest: hash.finish(),
        })
    }
    pub fn stage(scratch: &crate::limits::Scratch, text: &str) -> Result<Self, Error> {
        crate::limits::check(
            crate::limits::FILE,
            text.len() as u64,
            crate::limits::file_bytes()?,
        )?;
        Self::open(scratch.save(text)?)
    }
    pub fn text(&self) -> Result<String, Error> {
        crate::limits::read_file(&self.file)
    }
    pub fn same(&self, other: &Self) -> bool {
        self.bytes == other.bytes && self.digest == other.digest
    }
}

struct Hash(Sha256);
impl Hash {
    fn finish(self) -> String {
        self.0
            .finalize()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect()
    }
}
impl Write for Hash {
    fn write(&mut self, data: &[u8]) -> io::Result<usize> {
        self.0.update(data);
        Ok(data.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

// serde_json's string encoding, streamed over already-validated UTF-8 files.
fn quoted(file: &Path, out: &mut impl Write) -> io::Result<()> {
    out.write_all(b"\"")?;
    let mut reader = BufReader::new(File::open(file)?);
    loop {
        let bytes = reader.fill_buf()?;
        if bytes.is_empty() {
            break;
        }
        let mut start = 0;
        for (i, byte) in bytes.iter().copied().enumerate() {
            let escape: Option<&[u8]> = match byte {
                b'"' => Some(b"\\\""),
                b'\\' => Some(b"\\\\"),
                b'\n' => Some(b"\\n"),
                b'\r' => Some(b"\\r"),
                b'\t' => Some(b"\\t"),
                8 => Some(b"\\b"),
                12 => Some(b"\\f"),
                _ => None,
            };
            if escape.is_some() || byte < 32 {
                out.write_all(&bytes[start..i])?;
                if let Some(escape) = escape {
                    out.write_all(escape)?;
                } else {
                    write!(out, "\\u{byte:04x}")?;
                }
                start = i + 1;
            }
        }
        out.write_all(&bytes[start..])?;
        let length = bytes.len();
        reader.consume(length);
    }
    out.write_all(b"\"")
}

pub(super) fn fingerprint(files: &Files) -> Result<String, Error> {
    let mut hash = Hash(Sha256::new());
    hash.write_all(b"{")?;
    for (i, (name, artifact)) in files.iter().enumerate() {
        if i != 0 {
            hash.write_all(b",")?;
        }
        serde_json::to_writer(&mut hash, name).map_err(io::Error::other)?;
        hash.write_all(b":")?;
        quoted(&artifact.file, &mut hash)?;
    }
    hash.write_all(b"}")?;
    Ok(hash.finish())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn old_receipt_fingerprints_survive_streaming() {
        let scratch = crate::limits::Scratch::new().unwrap();
        let texts: BTreeMap<String, String> = BTreeMap::from([
            ("a/é.md".into(), "é\n\r\t\0\u{8}\u{c}\\\"".to_string()),
            ("b.md".into(), "文".repeat(10_000)),
        ]);
        let files = texts
            .iter()
            .map(|(name, text)| (name.clone(), Artifact::stage(&scratch, text).unwrap()))
            .collect();
        assert_eq!(
            fingerprint(&files).unwrap(),
            crate::knowledge::model::digest(serde_json::to_vec(&texts).unwrap())
        );
    }
}
