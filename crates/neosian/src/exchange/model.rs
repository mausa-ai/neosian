use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::knowledge::model::{digest, invalid};
use crate::store::{Document, error::Error, grammar, time::Stamp};

pub const MAX_BYTES: usize = 20 * 1024 * 1024;
pub const MAX_PATHS: usize = 1000;
pub const MAX_EVENTS: usize = 10000;

// These format-1 fields accept null, but omission must not silently default.
fn required<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer)
}

pub fn selected(path: &str) -> bool {
    let folded = path.to_ascii_lowercase();
    crate::memory::search::searchable(&folded)
        && !matches!(folded.as_str(), "sessions" | "messages" | "message-links")
}

fn path(text: &str) -> Result<(), Error> {
    grammar::validate_document_path(text)?;
    if !selected(text) {
        return Err(invalid("exchange excludes lifecycle paths"));
    }
    Ok(())
}

fn stamp(text: &str) -> Result<(), Error> {
    if !text.ends_with('Z') || Stamp::parse(text).is_err() {
        return Err(invalid("exchange timestamps must be UTC Z"));
    }
    Ok(())
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Checkpoint {
    pub format: u32,
    pub scope: String,
    pub stream: String,
    pub cursor: String,
}

impl Checkpoint {
    pub fn validate(&self) -> Result<(), Error> {
        grammar::parse_scope(&self.scope)?;
        if self.format != 1
            || !crate::changes::model::uuid(&self.stream)
            || self.cursor.is_empty()
            || self.cursor.len() > 2048
        {
            return Err(invalid("invalid exchange checkpoint"));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Record {
    pub content: String,
    pub version: u64,
    pub created_at: String,
    pub updated_at: String,
    #[serde(deserialize_with = "required")]
    pub actor: Option<String>,
    pub redacted: bool,
    pub extra: Map<String, Value>,
}

impl Record {
    pub fn from_document(scope: &str, path: &str, doc: &Document) -> Result<Self, Error> {
        let mut value = crate::wire::encode::document(scope, path, doc)?;
        let object = value.as_object_mut().expect("document object");
        object.remove("scope");
        object.remove("path");
        serde_json::from_value(value).map_err(|e| invalid(e.to_string()))
    }

    pub fn document(&self) -> Result<Document, Error> {
        crate::remote::codec::document(&serde_json::to_value(self).expect("record serializes"))
    }

    pub(super) fn validate(&self) -> Result<(), Error> {
        stamp(&self.created_at)?;
        stamp(&self.updated_at)?;
        if self.version == 0
            || self.version >= i64::MAX as u64
            || self.content.len() > 1024 * 1024
            || (self.redacted && !self.content.is_empty())
            || self.extra.keys().any(|key| {
                matches!(
                    key.as_str(),
                    "neosian_format"
                        | "version"
                        | "created_at"
                        | "updated_at"
                        | "actor"
                        | "redacted"
                )
            })
        {
            return Err(invalid("invalid exchange document"));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Deletion {
    pub version: u64,
    #[serde(deserialize_with = "required")]
    pub actor: Option<String>,
    pub created_at: String,
    pub redacted: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Update {
    pub path: String,
    #[serde(deserialize_with = "required")]
    pub document: Option<Record>,
    #[serde(deserialize_with = "required")]
    pub deletion: Option<Deletion>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Erasure {
    #[serde(deserialize_with = "required")]
    pub path: Option<String>,
    pub created_at: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Bundle {
    pub format: u32,
    pub id: String,
    #[serde(deserialize_with = "required")]
    pub base: Option<Checkpoint>,
    pub checkpoint: Checkpoint,
    pub updates: Vec<Update>,
    pub erasures: Vec<Erasure>,
}

impl Bundle {
    pub fn digest(&self) -> String {
        let mut value = serde_json::to_value(self).expect("bundle serializes");
        value.as_object_mut().expect("bundle object").remove("id");
        value.sort_all_objects();
        digest(serde_json::to_vec(&value).expect("bundle serializes"))
    }

    pub fn validate(&self) -> Result<(), Error> {
        self.checkpoint.validate()?;
        if let Some(base) = &self.base {
            base.validate()?;
            if base.scope != self.checkpoint.scope || base.stream != self.checkpoint.stream {
                return Err(invalid(
                    "exchange source identity changed; snapshot_required",
                ));
            }
            if base == &self.checkpoint && (!self.updates.is_empty() || !self.erasures.is_empty()) {
                return Err(invalid("unchanged checkpoint cannot carry changes"));
            }
        } else if !self.erasures.is_empty() {
            return Err(invalid("snapshot cannot carry incremental erasures"));
        }
        if self.format != 1
            || self.id != self.digest()
            || self.updates.len() > MAX_PATHS
            || self.erasures.len() > MAX_EVENTS
            || serde_json::to_vec(self).expect("bundle serializes").len() > MAX_BYTES
        {
            return Err(invalid("invalid exchange format, digest or size"));
        }
        let mut names = std::collections::BTreeSet::new();
        for update in &self.updates {
            path(&update.path)?;
            if !names.insert(update.path.to_lowercase()) {
                return Err(invalid("duplicate or case-aliased exchange path"));
            }
            match (&update.document, &update.deletion) {
                (Some(document), None) => document.validate()?,
                (None, Some(deletion)) if self.base.is_some() => {
                    stamp(&deletion.created_at)?;
                    if deletion.version == 0 || deletion.version >= i64::MAX as u64 {
                        return Err(invalid("invalid exchange deletion version"));
                    }
                }
                _ => return Err(invalid("exchange update requires exactly one state")),
            }
        }
        for erasure in &self.erasures {
            if let Some(name) = &erasure.path {
                path(name)?;
            }
            stamp(&erasure.created_at)?;
        }
        Ok(())
    }
}
