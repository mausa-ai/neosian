//! Versioned lexical search over current memory, shared by all storage backends.

pub mod cli;
mod matching;
pub mod mounted;
#[cfg(test)]
mod tests;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::store::cursor::{self, Kind};
use crate::store::error::{Error, MemoryError};
use crate::store::grammar::{parse_scope, validate_document_path};
use crate::store::{Document, envelope::Envelope};

fn fields() -> Vec<String> {
    vec!["path".into(), "content".into()]
}
fn limit() -> usize {
    20
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
        Ok(())
    }

    pub fn fingerprint(&self, target: &Value) -> String {
        let mut selection = json!([
            target,
            self.query,
            self.prefix,
            self.fields,
            self.metadata_keys
        ]);
        if !self.knowledge_policy.is_all() {
            selection
                .as_array_mut()
                .expect("selection array")
                .push(json!(self.knowledge_policy));
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

    /// Validate an optional backend's page before forwarding its references.
    pub fn check_page(&self, scope: &str, page: &Page) -> Result<(), Error> {
        self.validate_backend(scope)?;
        let mut last = String::new();
        let bad = || MemoryError::configuration("malformed memory-search version 1 response");
        if page.hits.len() > self.limit {
            return Err(bad().into());
        }
        for hit in &page.hits {
            if hit.scope != scope
                || hit.path <= last
                || !hit.path.starts_with(&self.prefix)
                || !searchable(&hit.path)
                || validate_document_path(&hit.path).is_err()
                || hit.version == 0
                || hit.reference.is_some()
                || hit.source.is_some()
                || hit.workspace_id.is_some()
                || hit.excerpt.text.chars().count() > 240
                || !self.fields.contains(&hit.excerpt.field)
                || hit.matched_fields.is_empty()
                || hit
                    .matched_fields
                    .iter()
                    .enumerate()
                    .any(|(i, f)| !self.fields.contains(f) || hit.matched_fields[..i].contains(f))
                || !hit.matched_fields.contains(&hit.excerpt.field)
                || crate::store::time::Stamp::parse(&hit.created_at).is_err()
                || crate::store::time::Stamp::parse(&hit.updated_at).is_err()
                || !hit.created_at.ends_with('Z')
                || !hit.updated_at.ends_with('Z')
                || match &hit.excerpt.metadata_key {
                    Some(key) => {
                        hit.excerpt.field != "metadata" || !self.metadata_keys.contains(key)
                    }
                    None => hit.excerpt.field == "metadata",
                }
            {
                return Err(bad().into());
            }
            last.clone_from(&hit.path);
        }
        if let Some(next) = &page.next_cursor
            && (page.hits.len() != self.limit
                || next.is_empty()
                || next.len() > 4096
                || self.cursor.as_ref() == Some(next))
        {
            return Err(bad().into());
        }
        Ok(())
    }
}

pub fn searchable(path: &str) -> bool {
    !crate::memory::mounts::state_path(path)
        && !path.starts_with("sessions/")
        && path != crate::conversation::sessions::HANDOFF_PATH
}

/// The caller supplies current documents in ascending path order under its read boundary.
pub fn scan(
    scope: &str,
    request: &Request,
    documents: impl Iterator<Item = Result<(String, Document), Error>>,
) -> Result<Page, Error> {
    let after = request.scope_after(scope)?;
    let terms = matching::terms(&request.query);
    let now = crate::store::time::Stamp::now();
    let mut page = Page {
        hits: Vec::new(),
        next_cursor: None,
    };
    for row in documents {
        let (path, document) = row?;
        if path <= after
            || !path.starts_with(&request.prefix)
            || !searchable(&path)
            || document.envelope.redacted
        {
            continue;
        }
        if !crate::knowledge::governance::eligible(
            request.knowledge_policy,
            scope,
            &path,
            &document.content,
            now,
        ) {
            continue;
        }
        if let Some((excerpt, matched_fields)) =
            matching::find(scope, &path, &document, request, &terms)?
        {
            if page.hits.len() == request.limit {
                page.next_cursor = Some(request.cursor_for(
                    &json!(scope),
                    "memory-search-v1",
                    &page.hits.last().expect("nonempty").path,
                ));
                break;
            }
            let Envelope {
                version,
                created_at,
                updated_at,
                ..
            } = &document.envelope;
            page.hits.push(Hit {
                scope: scope.into(),
                path,
                version: *version,
                created_at: created_at.utc().z(),
                updated_at: updated_at.utc().z(),
                actor: document.envelope.actor_value(),
                matched_fields,
                excerpt,
                reference: None,
                source: None,
                workspace_id: None,
            });
        }
    }
    Ok(page)
}
