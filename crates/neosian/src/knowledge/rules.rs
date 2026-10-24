//! Optional collection checks; these are data rules, never access policy.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use super::model::{MAX_METADATA, invalid};
use crate::store::error::Error;

const MARKER: &str = "<!-- neosian-collection:1\n";

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Definition {
    pub format: u32,
    pub name: String,
    #[serde(default)]
    pub schema: Option<Rules>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Rules {
    pub fields: BTreeMap<String, Field>,
    #[serde(default = "yes")]
    pub allow_unknown: bool,
}

fn yes() -> bool {
    true
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Field {
    #[serde(rename = "type")]
    pub kind: String,
    #[serde(default)]
    pub required: bool,
    #[serde(default)]
    pub values: Option<Vec<Value>>,
}

#[derive(Serialize)]
pub struct Issue {
    pub path: String,
    pub field: String,
    pub message: String,
}

fn typed(kind: &str, value: &Value) -> bool {
    match kind {
        "string" => value.is_string(),
        "integer" => value.is_i64() || value.is_u64(),
        "number" => value.is_number(),
        "boolean" => value.is_boolean(),
        "array" => value.is_array(),
        "object" => value.is_object(),
        "null" => value.is_null(),
        _ => false,
    }
}

impl Definition {
    pub fn validate(&self) -> Result<(), Error> {
        if self.format != 1 || self.name.is_empty() || self.name.chars().count() > 256 {
            return Err(invalid(
                "collection requires format 1 and a name of 1..256 characters",
            ));
        }
        if self.render().len() > MAX_METADATA {
            return Err(invalid("collection definition exceeds 64 KiB"));
        }
        if let Some(schema) = &self.schema {
            if schema.fields.len() > 64 {
                return Err(invalid("collection permits at most 64 fields"));
            }
            for (name, field) in &schema.fields {
                if name.is_empty()
                    || name.chars().count() > 128
                    || ![
                        "string", "integer", "number", "boolean", "array", "object", "null",
                    ]
                    .contains(&field.kind.as_str())
                {
                    return Err(invalid(format!("invalid field name or type: {name}")));
                }
                if let Some(values) = &field.values
                    && (values.is_empty()
                        || values.len() > 64
                        || values.iter().any(|v| !typed(&field.kind, v)))
                {
                    return Err(invalid(format!(
                        "field {name}: values must contain 1..64 entries of its type"
                    )));
                }
            }
        }
        Ok(())
    }

    pub fn parse(text: &str) -> Result<Self, Error> {
        let (json, body) = super::model::header(text, MARKER.trim_end_matches('\n'))?;
        if !body.is_empty() {
            return Err(invalid(
                "collection definitions cannot contain a trailing body",
            ));
        }
        Self::from_json(json)
    }

    pub fn from_json(json: &str) -> Result<Self, Error> {
        if json.len() > MAX_METADATA {
            return Err(invalid("collection exceeds 64 KiB"));
        }
        let definition: Self = super::json::parse(json)?;
        definition.validate()?;
        Ok(definition)
    }

    pub fn render(&self) -> String {
        format!(
            "{MARKER}{}\n-->\n",
            serde_json::to_string_pretty(self)
                .expect("definition serializes")
                .replace('>', "\\u003e")
        )
    }

    pub fn check(&self, path: &str, attributes: &Map<String, Value>) -> Vec<Issue> {
        let mut issues = vec![];
        let mut add = |field: &str, message: String| {
            issues.push(Issue {
                path: path.into(),
                field: field.into(),
                message,
            })
        };
        if let Some(schema) = &self.schema {
            for (name, rule) in &schema.fields {
                match attributes.get(name) {
                    None if rule.required => add(name, "required field is missing".into()),
                    Some(value) if !typed(&rule.kind, value) => {
                        add(name, format!("expected {}", rule.kind))
                    }
                    Some(value) if rule.values.as_ref().is_some_and(|v| !v.contains(value)) => {
                        add(name, "value is outside the allowed values".into())
                    }
                    _ => {}
                }
            }
            if !schema.allow_unknown {
                for name in attributes
                    .keys()
                    .filter(|k| !schema.fields.contains_key(*k))
                {
                    add(name, "unknown field is not allowed".into());
                }
            }
        }
        issues
    }
}
