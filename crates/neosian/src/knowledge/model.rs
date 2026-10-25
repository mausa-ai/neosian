//! Readable, versioned knowledge metadata lives with the document body.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

use crate::store::error::Error;
use crate::store::grammar::{parse_scope, validate_document_path};

pub const MAX_BODY: usize = 1024 * 1024;
pub const MAX_TOTAL: usize = 20 * MAX_BODY;
pub const MAX_METADATA: usize = 64 * 1024;
pub const DEFINITION: &str = ".collection";
const MARKER: &str = "<!-- neosian-knowledge:";

pub fn invalid(message: impl Into<String>) -> Error {
    Error::Value(message.into())
}

pub fn digest(bytes: impl AsRef<[u8]>) -> String {
    Sha256::digest(bytes.as_ref())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

pub fn header<'a>(text: &'a str, marker: &str) -> Result<(&'a str, &'a str), Error> {
    let remaining = text
        .strip_prefix(marker)
        .and_then(|s| s.strip_prefix("\r\n").or_else(|| s.strip_prefix('\n')))
        .ok_or_else(|| invalid("unsupported marked header; expected version 1 and a line break"))?;
    ["\n-->\n", "\r\n-->\r\n"]
        .into_iter()
        .filter_map(|ending| remaining.split_once(ending))
        .min_by_key(|(head, _)| head.len())
        .ok_or_else(|| invalid("marked header is missing its closing delimiter"))
}

pub fn ordinary(path: &str) -> Result<(), Error> {
    validate_document_path(path)?;
    // A case-folding filesystem must not alias an imported path into a lifecycle.
    let folded = path.to_ascii_lowercase();
    if !crate::memory::search::searchable(&folded)
        || folded.split('/').any(|p| p == DEFINITION)
        || matches!(folded.as_str(), "sessions" | "messages" | "message-links")
    {
        return Err(invalid(format!("reserved knowledge path: {path}")));
    }
    Ok(())
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Reference {
    pub relation: String,
    pub path: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scope: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Source {
    pub label: String,
    pub file: String,
    pub sha256: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub origin: Option<Origin>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Origin {
    pub scope: String,
    pub path: String,
    pub version: u64,
    pub created_at: String,
    pub updated_at: String,
    pub actor: Option<String>,
    pub extra: Map<String, Value>,
}

impl Origin {
    pub fn validate(&self) -> Result<(), Error> {
        parse_scope(&self.scope)?;
        ordinary(&self.path)?;
        if self.version == 0 {
            return Err(invalid("source version must be positive"));
        }
        for stamp in [&self.created_at, &self.updated_at] {
            crate::store::time::Stamp::parse(stamp)
                .map_err(|_| invalid("source timestamp is malformed"))?;
            if !stamp.ends_with('Z') {
                return Err(invalid("source timestamps must be UTC Z"));
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Metadata {
    pub format: u32,
    pub collection: String,
    #[serde(default)]
    pub attributes: Map<String, Value>,
    #[serde(default)]
    pub references: Vec<Reference>,
    #[serde(default)]
    pub sources: Vec<Source>,
}

impl Metadata {
    pub fn empty(collection: &str) -> Self {
        Self {
            format: 1,
            collection: collection.into(),
            attributes: Map::new(),
            references: vec![],
            sources: vec![],
        }
    }

    pub fn validate(&self) -> Result<(), Error> {
        if self.format != 1 {
            return Err(invalid("unsupported knowledge metadata format; expected 1"));
        }
        ordinary(&self.collection)?;
        if self.references.len() > 128 || self.sources.len() > 128 {
            return Err(invalid(
                "at most 128 references and 128 sources are supported",
            ));
        }
        for reference in &self.references {
            ordinary(&reference.path)?;
            if reference.relation.is_empty() || reference.relation.chars().count() > 128 {
                return Err(invalid("reference relation requires 1..128 characters"));
            }
            if let Some(scope) = &reference.scope {
                parse_scope(scope)?;
            } else {
                ordinary(&format!("{}/{}", self.collection, reference.path))?;
            }
        }
        for source in &self.sources {
            if source.label.is_empty()
                || source.label.chars().count() > 256
                || source.file.is_empty()
                || source.file.len() > 1024
                || source.sha256.len() != 64
                || !source
                    .sha256
                    .bytes()
                    .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
            {
                return Err(invalid("invalid source label, relative name or SHA-256"));
            }
            if let Some(origin) = &source.origin {
                origin.validate()?;
            }
        }
        if serde_json::to_vec(self).expect("metadata serializes").len() > MAX_METADATA {
            return Err(invalid("knowledge metadata exceeds 64 KiB"));
        }
        Ok(())
    }
}

#[derive(Clone)]
pub struct Content {
    pub metadata: Metadata,
    pub body: String,
}

impl Content {
    pub fn parse(text: &str, collection: &str) -> Result<Self, Error> {
        if text.contains('\0') {
            return Err(invalid("Markdown cannot contain a NUL byte"));
        }
        if text.len() > MAX_BODY {
            return Err(invalid("knowledge document exceeds 1 MiB"));
        }
        let (metadata, body) = if text.starts_with(MARKER) {
            let (header, body) = header(text, "<!-- neosian-knowledge:1")?;
            if header.len() > MAX_METADATA {
                return Err(invalid("knowledge header exceeds 64 KiB"));
            }
            let metadata: Metadata = super::json::parse(header)?;
            metadata.validate()?;
            (metadata, body)
        } else {
            (Metadata::empty(collection), text)
        };
        Ok(Self {
            metadata,
            body: body.into(),
        })
    }

    pub fn render(&self) -> Result<String, Error> {
        self.metadata.validate()?;
        let header = serde_json::to_string_pretty(&self.metadata)
            .expect("metadata serializes")
            .replace('>', "\\u003e");
        if header.len() > MAX_METADATA {
            return Err(invalid("knowledge header exceeds 64 KiB"));
        }
        let text = format!("<!-- neosian-knowledge:1\n{header}\n-->\n{}", self.body);
        if text.len() > MAX_BODY {
            return Err(invalid("document including knowledge header exceeds 1 MiB"));
        }
        Ok(text)
    }
}
