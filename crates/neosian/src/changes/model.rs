use serde::{Deserialize, Serialize};

use crate::store::error::{Error, MemoryError};
use crate::store::grammar::{parse_scope, validate_document_path};
use crate::store::time::Stamp;

pub fn invalid(message: &str) -> Error {
    MemoryError::configuration(message).into()
}

pub fn uuid(text: &str) -> bool {
    uuid::Uuid::parse_str(text).is_ok_and(|id| id.to_string() == text)
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub enum Kind {
    #[serde(rename = "state.document_created")]
    Created,
    #[serde(rename = "state.document_modified")]
    Modified,
    #[serde(rename = "state.document_deleted")]
    Deleted,
    #[serde(rename = "state.document_renamed")]
    Renamed,
    #[serde(rename = "state.document_erased")]
    Erased,
    #[serde(rename = "state.scope_erased")]
    ScopeErased,
    #[serde(rename = "state.scope_reset")]
    Reset,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Head {
    pub format: u32,
    pub stream: String,
    pub sequence: u64,
    pub floor: u64,
}

impl Head {
    pub fn new() -> Self {
        Self {
            format: 1,
            stream: uuid::Uuid::new_v4().to_string(),
            sequence: 0,
            floor: 0,
        }
    }

    pub fn validate(&self) -> Result<(), Error> {
        if self.format != 1
            || !uuid(&self.stream)
            || self.floor > self.sequence
            || self.sequence > i64::MAX as u64
        {
            return Err(invalid("invalid or unsupported change-stream head"));
        }
        Ok(())
    }

    pub fn event(&mut self, scope: &str, change: Change) -> Result<Event, Error> {
        self.sequence = self
            .sequence
            .checked_add(1)
            .filter(|n| *n <= i64::MAX as u64)
            .ok_or_else(|| invalid("change sequence exhausted"))?;
        let event = Event {
            format: 1,
            id: format!("{}:{}", self.stream, self.sequence),
            stream: self.stream.clone(),
            sequence: self.sequence,
            scope: scope.into(),
            change,
        };
        event.validate()?;
        Ok(event)
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Change {
    pub kind: Kind,
    pub path: Option<String>,
    pub version: Option<u64>,
    pub previous_path: Option<String>,
    pub previous_version: Option<u64>,
    pub created_at: String,
    pub reason: Option<String>,
}

impl Change {
    pub fn new(kind: Kind, path: Option<&str>, version: Option<u64>) -> Self {
        Self {
            kind,
            path: path.map(str::to_string),
            version,
            previous_path: None,
            previous_version: None,
            created_at: Stamp::now().utc().z(),
            reason: None,
        }
    }

    pub fn reset(reason: &str) -> Self {
        Self {
            reason: Some(reason.into()),
            ..Self::new(Kind::Reset, None, None)
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct Event {
    pub format: u32,
    pub id: String,
    pub stream: String,
    pub sequence: u64,
    pub scope: String,
    #[serde(flatten)]
    pub change: Change,
}

impl Event {
    pub fn validate(&self) -> Result<(), Error> {
        parse_scope(&self.scope)?;
        let c = &self.change;
        for path in c.path.iter().chain(&c.previous_path) {
            validate_document_path(path)?;
        }
        let shape = match c.kind {
            Kind::Created | Kind::Modified | Kind::Deleted => {
                c.path.is_some()
                    && c.version.is_some()
                    && c.previous_path.is_none()
                    && c.reason.is_none()
            }
            Kind::Renamed => {
                c.path.is_some()
                    && c.version.is_some()
                    && c.previous_path.is_some()
                    && c.path != c.previous_path
                    && c.previous_version.is_some()
                    && c.reason.is_none()
            }
            Kind::Erased => {
                c.path.is_some()
                    && c.version.is_none()
                    && c.previous_path.is_none()
                    && c.reason.is_none()
            }
            Kind::ScopeErased => {
                c.path.is_none()
                    && c.version.is_none()
                    && c.previous_path.is_none()
                    && c.reason.is_none()
            }
            Kind::Reset => {
                c.path.is_none()
                    && c.version.is_none()
                    && c.previous_path.is_none()
                    && matches!(c.reason.as_deref(), Some("tracking_started" | "restored"))
            }
        };
        if !shape
            || self.format != 1
            || !uuid(&self.stream)
            || self.sequence == 0
            || self.sequence > i64::MAX as u64
            || self.id != format!("{}:{}", self.stream, self.sequence)
            || c.version == Some(0)
            || c.previous_version == Some(0)
            || (c.kind != Kind::Renamed && c.previous_version.is_some())
            || !c.created_at.ends_with('Z')
            || Stamp::parse(&c.created_at).is_err()
        {
            return Err(invalid("invalid or unsupported change event"));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    #[serde(default)]
    pub cursor: Option<String>,
    #[serde(default = "default_limit")]
    pub limit: usize,
}

fn default_limit() -> usize {
    50
}

impl Request {
    pub fn validate(&self) -> Result<(), Error> {
        if !(1..=100).contains(&self.limit)
            || self
                .cursor
                .as_ref()
                .is_some_and(|c| c.is_empty() || c.len() > 2048)
        {
            return Err(invalid(
                "changes require limit 1..100 and a cursor of at most 2048 bytes",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Page {
    pub format: u32,
    pub scope: String,
    pub stream: Option<String>,
    pub events: Vec<Event>,
    pub cursor: Option<String>,
    pub head_cursor: Option<String>,
    pub earliest_cursor: Option<String>,
    pub has_more: bool,
    pub gap: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Pruned {
    pub format: u32,
    pub scope: String,
    pub removed: u64,
    pub write: bool,
    pub earliest_cursor: String,
}
