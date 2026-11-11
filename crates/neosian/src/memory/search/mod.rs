//! Versioned lexical search over current memory, shared by all storage backends.

mod check;
pub mod cli;
mod matching;
mod mounted_rank;
mod rank;
mod scan;
pub use scan::{Scan, scan};
pub mod mounted;
#[cfg(test)]
mod tests;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::store::cursor::{self, Kind};
use crate::store::error::Error;
use crate::store::grammar::{parse_scope, validate_document_path};

fn fields() -> Vec<String> {
    vec!["path".into(), "content".into()]
}
fn limit() -> usize {
    20
}

/// Result order. Absent on the wire means version 1 path order; memory ranking
/// version 1 adds explicit orders, echoed in the response.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize, clap::ValueEnum)]
#[serde(rename_all = "lowercase")]
pub enum Order {
    Path,
    Relevance,
    Newest,
}

impl Order {
    pub fn ranked(self) -> bool {
        self != Order::Path
    }
    pub fn name(self) -> &'static str {
        match self {
            Order::Path => "path",
            Order::Relevance => "relevance",
            Order::Newest => "newest",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub query: String,
    #[serde(default)]
    pub prefix: String,
    #[serde(default = "fields")]
    pub fields: Vec<String>,
    #[serde(default)]
    pub metadata_keys: Vec<String>,
    #[serde(default = "limit")]
    pub limit: usize,
    #[serde(default)]
    pub cursor: Option<String>,
    #[serde(
        default,
        skip_serializing_if = "crate::knowledge::governance::Policy::is_all"
    )]
    pub knowledge_policy: crate::knowledge::governance::Policy,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub order: Option<Order>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub updated_since: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub updated_before: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Excerpt {
    pub field: String,
    pub metadata_key: Option<String>,
    pub offset: usize,
    pub text: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Hit {
    pub scope: String,
    pub path: String,
    pub version: u64,
    pub created_at: String,
    pub updated_at: String,
    pub actor: Option<String>,
    pub matched_fields: Vec<String>,
    pub excerpt: Excerpt,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub score: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reference: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub workspace_id: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Page {
    pub hits: Vec<Hit>,
    pub next_cursor: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub order: Option<Order>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fallback: Option<String>,
}

impl Page {
    fn empty(order: Option<Order>) -> Self {
        Self {
            hits: Vec::new(),
            next_cursor: None,
            order,
            fallback: None,
        }
    }
}

impl Request {
    pub fn validate(&self) -> Result<(), String> {
        let terms = self.query.split_whitespace().count();
        if !(1..=1024).contains(&self.query.chars().count()) || !(1..=16).contains(&terms) {
            return Err("query requires 1..1024 characters and 1..16 whitespace terms".into());
        }
        if !(1..=100).contains(&self.limit) {
            return Err("limit must be between 1 and 100".into());
        }
        let unique = |items: &[String]| {
            let mut seen = std::collections::HashSet::new();
            items.iter().all(|item| seen.insert(item))
        };
        if self.fields.is_empty()
            || self.fields.len() > 3
            || !unique(&self.fields)
            || self
                .fields
                .iter()
                .any(|f| !["path", "content", "metadata"].contains(&f.as_str()))
        {
            return Err("fields must select distinct path, content or metadata entries".into());
        }
        if self.metadata_keys.len() > 8
            || !unique(&self.metadata_keys)
            || self
                .metadata_keys
                .iter()
                .any(|k| !(1..=128).contains(&k.chars().count()))
            || self.fields.iter().any(|f| f == "metadata") == self.metadata_keys.is_empty()
        {
            return Err("metadata requires 1..8 distinct metadata_keys of 1..128 characters; keys require the metadata field".into());
        }
        if self.prefix.len() > 1024 || self.cursor.as_ref().is_some_and(|c| c.len() > 8192) {
            return Err("search prefix or cursor exceeds its size limit".into());
        }
        if self.cursor.is_some() && self.order.is_some_and(Order::ranked) {
            return Err("search cursors continue path order only".into());
        }
        if let (Some(since), Some(before)) = self.bounds()?
            && since >= before
        {
            return Err("updated_since must precede updated_before".into());
        }
        Ok(())
    }

    /// The UTC instants of the date bounds, in microseconds.
    pub fn bounds(&self) -> Result<(Option<i64>, Option<i64>), String> {
        let instant = |value: &Option<String>, name: &str| {
            value
                .as_deref()
                .map(|text| {
                    crate::store::time::Stamp::parse(text)
                        .ok()
                        .filter(|_| text.ends_with('Z'))
                        .map(crate::store::time::Stamp::instant)
                        .ok_or_else(|| format!("{name} must be a UTC RFC 3339 instant ending in Z"))
                })
                .transpose()
        };
        Ok((
            instant(&self.updated_since, "updated_since")?,
            instant(&self.updated_before, "updated_before")?,
        ))
    }

    /// Whether the request uses memory ranking version 1 fields.
    pub fn ranking(&self) -> bool {
        self.order.is_some() || self.updated_since.is_some() || self.updated_before.is_some()
    }

    pub fn fingerprint(&self, target: &Value) -> String {
        let mut selection = json!([
            target,
            self.query,
            self.prefix,
            self.fields,
            self.metadata_keys
        ]);
        let items = selection.as_array_mut().expect("selection array");
        if !self.knowledge_policy.is_all() {
            items.push(json!(self.knowledge_policy));
        }
        // Ranking fields bind only when present, so version 1 cursors stay valid.
        for (name, value) in [
            ("order", json!(self.order)),
            ("updated_since", json!(self.updated_since)),
            ("updated_before", json!(self.updated_before)),
        ] {
            if !value.is_null() {
                items.push(json!([name, value]));
            }
        }
        let bytes = serde_json::to_vec(&selection).expect("search values serialize");
        Sha256::digest(bytes)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect()
    }

    pub fn after(&self, target: &Value, method: &str) -> Result<String, Error> {
        self.validate().map_err(Error::Value)?;
        let Some(token) = &self.cursor else {
            return Ok(String::new());
        };
        let parts = cursor::decode(method, token, &[Kind::Str, Kind::Str]).map_err(Error::Value)?;
        if parts[0] != self.fingerprint(target) {
            return Err(Error::Value(
                "search cursor belongs to a different query or selection".into(),
            ));
        }
        Ok(parts[1].as_str().expect("cursor string").into())
    }

    pub fn cursor_for(&self, target: &Value, method: &str, path: &str) -> String {
        cursor::encode(method, &[json!(self.fingerprint(target)), json!(path)])
    }

    pub fn scope_after(&self, scope: &str) -> Result<String, Error> {
        self.validate_backend(scope)?;
        let after = self.after(&json!(scope), "memory-search-v1")?;
        if !after.is_empty() {
            validate_document_path(&after)?;
            if !after.starts_with(&self.prefix) || !searchable(&after) {
                return Err(Error::Value("invalid memory-search cursor position".into()));
            }
        }
        Ok(after)
    }

    pub fn validate_backend(&self, scope: &str) -> Result<(), Error> {
        self.validate().map_err(Error::Value)?;
        parse_scope(scope)?;
        if self
            .cursor
            .as_ref()
            .is_some_and(|c| c.is_empty() || c.len() > 4096)
        {
            return Err(Error::Value(
                "backend search cursor must contain 1..4096 bytes".into(),
            ));
        }
        Ok(())
    }
}

pub fn searchable(path: &str) -> bool {
    !crate::memory::mounts::state_path(path)
        && !path.starts_with("sessions/")
        && path != crate::conversation::sessions::HANDOFF_PATH
}
