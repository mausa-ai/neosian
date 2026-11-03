//! Native capture checks the copy and commits its receipt under the same root lock.
//! This is a private local primitive, not an extension to storage wire 6.

use super::error::{Error, MemoryError};
use super::grammar::{Actor, validate_document_path};
use super::transaction::WriteOp;
use super::{FileStore, layout};
use crate::native_memory::digest;
use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Read;

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Receipt {
    format: u32,
    path: String,
    version: u64,
    sha256: String,
}

pub(crate) struct NativeCapture<'a>(&'a FileStore);

impl NativeCapture<'_> {
    pub(crate) fn recovery_pending(&self) -> bool {
        self.0.root.join(".transactions/pending.json").exists()
    }

    pub(crate) fn write(
        &self,
        scope: &str,
        path: &str,
        source: &str,
        content: &str,
        actor: &Actor,
        write: bool,
    ) -> Result<(&'static str, Option<u64>), Error> {
        self.0
            .capture_native_locked(scope, path, source, content, actor, write)
    }
}

impl FileStore {
    /// Serialize source observation too: another registry using this store
    /// cannot land an older snapshot after a newer capture.
    pub(crate) fn with_native_capture<T>(
        &self,
        capture: impl FnOnce(NativeCapture<'_>) -> Result<T, Error>,
    ) -> Result<T, Error> {
        let _held = self.lock()?;
        capture(NativeCapture(self))
    }

    fn capture_native_locked(
        &self,
        scope: &str,
        path: &str,
        source: &str,
        content: &str,
        actor: &Actor,
        write: bool,
    ) -> Result<(&'static str, Option<u64>), Error> {
        self.scope(scope)?;
        validate_document_path(path)?;
        if !path.starts_with("native-memory/")
            || source.len() != 64
            || !source
                .bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
        {
            return Err(Error::Value("invalid native memory capture target".into()));
        }
        let refuse = |reason| Error::from(MemoryError::conflict(scope, Some(path), reason));
        // Captured Markdown is opaque. Never bypass a collection's import rules.
        let mut prefix = String::new();
        for segment in path.split('/').take(path.split('/').count() - 1) {
            if !prefix.is_empty() {
                prefix.push('/');
            }
            prefix.push_str(segment);
            let definition = format!("{prefix}/.collection");
            if layout::doc_file(&self.root, scope, &definition)?.exists() {
                return Err(refuse("native_memory_collection_requires_explicit_import"));
            }
        }
        let file = layout::doc_file(&self.root, scope, path)?;
        let existing = self.present(scope, path, &file)?;
        let receipt_file = layout::scope_dir(&self.root, scope)
            .join(".native-memory")
            .join(source)
            .join(format!("{}.json", digest(path.as_bytes())));
        // Validate containment and symlinks with the redo writer's same path checks.
        let receipt_op = |text| WriteOp::put(&self.root, &receipt_file, text);
        receipt_op(String::new())?;
        let receipt: Option<Receipt> = match fs::File::open(&receipt_file) {
            Ok(file) => {
                let mut text = String::new();
                file.take(16385).read_to_string(&mut text)?;
                if text.len() > 16384 {
                    return Err(refuse("native_memory_receipt_invalid"));
                }
                Some(
                    serde_json::from_str(&text)
                        .map_err(|_| refuse("native_memory_receipt_invalid"))?,
                )
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
            Err(e) => return Err(e.into()),
        };
        let expected = match (receipt.as_ref(), existing.as_ref()) {
            (_, Some(doc)) if doc.envelope.redacted => {
                return Err(refuse("native_memory_destination_erased"));
            }
            (Some(receipt), Some(doc)) => {
                if receipt.format != 1
                    || receipt.path != path
                    || receipt.version != doc.envelope.version
                    || receipt.sha256 != digest(doc.content.as_bytes())
                {
                    return Err(refuse("native_memory_destination_changed"));
                }
                if doc.content == content {
                    return Ok(("unchanged", Some(doc.envelope.version)));
                }
                Some(doc.envelope.version)
            }
            (Some(_), None) => return Err(refuse("native_memory_destination_deleted")),
            (None, Some(_)) => return Err(refuse("native_memory_destination_occupied")),
            (None, None) => {
                if !self.rows(scope, path)?.1.is_empty() {
                    return Err(refuse("native_memory_destination_has_history"));
                }
                None
            }
        };
        let action = if expected.is_some() {
            "replace"
        } else {
            "create"
        };
        if !write {
            return Ok((action, expected));
        }
        let (envelope, mut writes, change) =
            self.prepare_write(scope, path, content, actor, expected)?;
        let receipt = Receipt {
            format: 1,
            path: path.into(),
            version: envelope.version,
            sha256: digest(content.as_bytes()),
        };
        writes.push(receipt_op(
            serde_json::to_string(&receipt).map_err(std::io::Error::other)?,
        )?);
        self.commit_changes(scope, writes, change)?;
        Ok((action, Some(envelope.version)))
    }
}
