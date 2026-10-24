//! The memory ABC; mutations return their own statement's document snapshot.

use super::{PostgresStore, rows, sql};
use crate::remote::codec;
use crate::state::Deleted;
use crate::store::error::{Error, MemoryError};
use crate::store::grammar::{Actor, parse_scope};
use crate::store::journal::{Act, Row};
use crate::store::time::Stamp;
use crate::store::{Document, Entry, FORMAT};
use serde_json::{Value, json};

impl PostgresStore {
    pub fn read(&self, scope: &str, path: &str) -> Result<Option<Document>, Error> {
        rows::address(scope, path)?;
        self.fetch(
            sql!("store/read_document"),
            json!({"scope":scope,"path":path}),
        )?
        .first()
        .map(|row| rows::document(scope, path, row))
        .transpose()
    }

    pub fn write_document(
        &self,
        scope: &str,
        path: &str,
        content: &str,
        actor: &Actor,
        expected: Option<u64>,
    ) -> Result<Document, Error> {
        rows::address(scope, path)?;
        // The wire represents negative expectations as MAX; they still conflict.
        let expectation = expected.map(|v| i32::try_from(v).unwrap_or(-1));
        let mut row = self.one(
            sql!("store/write_document"),
            json!({
                "scope":scope,"path":path,"content":content,"actor":actor.as_str(),
                "expected":expectation,"now":Stamp::now().z(),"format":FORMAT
            }),
            true,
        )?;
        guard(scope, path, &row)?;
        if row["version"].is_null() {
            let reason = if row["existed"] == true {
                "version_mismatch"
            } else {
                "document_absent"
            };
            return Err(MemoryError::conflict_versions(
                scope,
                Some(path),
                reason,
                expected,
                row["current_version"].as_u64(),
            )
            .into());
        }
        row["content"] = content.into();
        row["actor"] = actor.as_str().into();
        row["redacted"] = false.into();
        row["neosian_format"] = FORMAT.into();
        rows::document(scope, path, &row)
    }

    pub fn delete(&self, scope: &str, path: &str, actor: &Actor) -> Result<Deleted, Error> {
        rows::address(scope, path)?;
        let row = self.one(sql!("store/delete_document"), json!({
            "scope":scope,"path":path,"actor":actor.as_str(),"now":Stamp::now().z(),"format":FORMAT
        }), true)?;
        guard(scope, path, &row)?;
        Ok(Deleted {
            removed: row["existed"] == true,
            version: None,
        })
    }

    pub fn rename_document(
        &self,
        scope: &str,
        src: &str,
        dst: &str,
        actor: &Actor,
    ) -> Result<Document, Error> {
        rows::address(scope, src)?;
        rows::address(scope, dst)?;
        let mut row = self.one(sql!("store/rename_document"), json!({
            "scope":scope,"src":src,"dst":dst,"actor":actor.as_str(),"now":Stamp::now().z(),"format":FORMAT
        }), true)?;
        if row["src_exists"] != true {
            return Err(MemoryError::not_found(scope, src).into());
        }
        if row["dst_exists"] == true {
            return Err(MemoryError::conflict(scope, Some(dst), "destination_exists").into());
        }
        guard(scope, src, &row)?;
        row["actor"] = actor.as_str().into();
        row["neosian_format"] = FORMAT.into();
        rows::document(scope, dst, &row)
    }

    pub fn list_documents(&self, scope: &str, prefix: &str) -> Result<Vec<Entry>, Error> {
        parse_scope(scope)?;
        self.fetch(
            sql!("store/list_documents"),
            json!({"scope":scope,"pattern":format!("{}%", rows::escaped(prefix))}),
        )?
        .iter()
        .map(|row| entry(scope, row))
        .collect()
    }

    pub fn versions(&self, scope: &str, path: &str, limit: usize) -> Result<Vec<Row>, Error> {
        rows::address(scope, path)?;
        self.fetch(
            sql!("store/read_versions"),
            json!({"scope":scope,"path":path,"limit":limit}),
        )?
        .iter()
        .map(|row| version(scope, row))
        .collect()
    }

    pub fn history(
        &self,
        scope: &str,
        since: Option<Stamp>,
        limit: Option<usize>,
    ) -> Result<Vec<Row>, Error> {
        parse_scope(scope)?;
        self.fetch(
            sql!("store/read_history"),
            json!({"scope":scope,"since":since.map(Stamp::z),"limit":limit}),
        )?
        .iter()
        .map(|row| version(scope, row))
        .collect()
    }

    pub fn redactions(
        &self,
        scope: &str,
        since: Option<Stamp>,
        limit: Option<usize>,
    ) -> Result<Vec<Act>, Error> {
        parse_scope(scope)?;
        self.fetch(
            sql!("store/read_redactions"),
            json!({"scope":scope,"since":since.map(Stamp::z),"limit":limit}),
        )?
        .iter()
        .map(codec::redaction)
        .collect()
    }

    pub fn redact(&self, scope: &str, path: Option<&str>, actor: &Actor) -> Result<usize, Error> {
        parse_scope(scope)?;
        if let Some(path) = path {
            rows::address(scope, path)?;
        }
        let row = self.one(
            sql!("store/redact"),
            json!({"scope":scope,"path":path,"actor":actor.as_str(),"now":Stamp::now().z(),"format":FORMAT}),
            false,
        )?;
        guard(
            scope,
            row["invalid_path"].as_str().unwrap_or_default(),
            &row,
        )?;
        Ok(codec::number(&row, "matched")? as usize)
    }
}

fn guard(scope: &str, path: &str, row: &Value) -> Result<(), Error> {
    if row["format_ok"] != true {
        return Err(MemoryError::format_unsupported(
            scope,
            path,
            &format!("neosian_format {} is not supported", row["current_format"]),
        )
        .into());
    }
    Ok(())
}

pub(super) fn entry(scope: &str, row: &Value) -> Result<Entry, Error> {
    rows::memory_format(scope, &codec::text(row, "path")?, row)?;
    codec::entry(row)
}

pub(super) fn version(scope: &str, row: &Value) -> Result<Row, Error> {
    rows::memory_format(scope, &codec::text(row, "path")?, row)?;
    codec::version(row)
}
