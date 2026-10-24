//! Optional references copied into a message, never authority or executable work.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::memory::dispatch::Memory;
use crate::memory::mounts::resolve;
use crate::store::grammar::{parse_scope, validate_document_path};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Reference {
    pub scope: String,
    pub path: String,
    pub version: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub change_id: Option<String>,
}

impl Reference {
    pub fn validate(&self) -> Result<(), String> {
        parse_scope(&self.scope).map_err(|error| error.message)?;
        validate_document_path(&self.path).map_err(|error| error.message)?;
        if self.version == 0 || self.version > i64::MAX as u64 {
            return Err("context version must be positive".into());
        }
        if let Some(id) = &self.change_id {
            let valid = id.split_once(':').is_some_and(|(stream, sequence)| {
                uuid::Uuid::parse_str(stream).is_ok_and(|uuid| uuid.to_string() == stream)
                    && sequence
                        .parse::<u64>()
                        .is_ok_and(|n| n > 0 && n <= i64::MAX as u64 && n.to_string() == sequence)
            });
            if !valid {
                return Err("context change id must be a stream UUID and positive sequence".into());
            }
        }
        Ok(())
    }
}

pub fn select(memory: &Memory, args: &Map<String, Value>) -> Result<Option<Reference>, String> {
    let Some(path) = args.get("context").and_then(Value::as_str) else {
        if ["context_version", "change_id"]
            .iter()
            .any(|key| args.get(*key).is_some_and(|value| !value.is_null()))
        {
            return Err("context_version and change_id require context".into());
        }
        return Ok(None);
    };
    let (mount, path) = resolve(&memory.mounts, path).map_err(|error| error.message)?;
    validate_document_path(path).map_err(|error| error.message)?;
    let document = memory
        .store
        .read(&mount.scope, path)
        .map_err(|error| crate::conversation::Error::from(error).text())?
        .filter(|document| !document.envelope.redacted)
        .ok_or("context document is missing or erased")?;
    let version = match args.get("context_version") {
        None | Some(Value::Null) => document.envelope.version,
        Some(value) => value.as_u64().ok_or("context_version must be positive")?,
    };
    if version != document.envelope.version {
        return Err("context version changed; read the document again".into());
    }
    let reference = Reference {
        scope: mount.scope.clone(),
        path: path.into(),
        version,
        change_id: args
            .get("change_id")
            .and_then(Value::as_str)
            .map(str::to_string),
    };
    reference.validate()?;
    Ok(Some(reference))
}
