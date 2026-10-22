//! Key-preserving, individually atomic edits of client-owned files.

use std::fs;
use std::path::Path;

use serde_json::{Value, json};

use super::targets::Target;
use crate::store::fileio;

pub const FIX: &str = "fix it by hand, or re-run without --write and paste the entry yourself";

pub struct Failure {
    pub message: String,
    pub hint: String,
}

impl Failure {
    pub fn new(message: impl Into<String>, hint: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            hint: hint.into(),
        }
    }

    pub fn payload(&self, target: &Target) -> Value {
        json!({"success": false, "error": self.message, "hint": self.hint,
            "client": target.client, "config_path": target.path})
    }
}

impl From<std::io::Error> for Failure {
    fn from(error: std::io::Error) -> Self {
        Self::new(error.to_string(), FIX)
    }
}

pub fn evidence(target: &Target) -> Result<(), Failure> {
    if target.evidence.is_dir() {
        return Ok(());
    }
    Err(Failure::new(
        format!(
            "{} is not installed here: {} does not exist",
            target.label,
            target.evidence.display()
        ),
        "install the client first — neosian never creates another program's config directory",
    ))
}

pub fn load(path: &Path) -> Result<Value, Failure> {
    if !path.exists() {
        return Ok(json!({}));
    }
    let text = fs::read_to_string(path)
        .map_err(|e| Failure::new(format!("cannot read {}: {e}", path.display()), FIX))?;
    let value: Value = serde_json::from_str(&text).map_err(|_| {
        Failure::new(
            format!(
                "{} is not valid JSON; refusing to rewrite it",
                path.display()
            ),
            FIX,
        )
    })?;
    if !value.is_object() {
        return Err(Failure::new(
            format!(
                "{} is not a JSON object; refusing to rewrite it",
                path.display()
            ),
            FIX,
        ));
    }
    Ok(value)
}

pub fn read(path: &Path) -> Option<Value> {
    let text = fs::read_to_string(path).ok()?;
    let value: Value = if path.extension().is_some_and(|s| s == "toml") {
        serde_json::to_value(text.parse::<toml::Value>().ok()?).ok()?
    } else {
        serde_json::from_str(&text).ok()?
    };
    value.is_object().then_some(value)
}

pub fn settings(path: &Path) -> Result<Value, Failure> {
    if !path.exists() {
        return Ok(json!({"schema_version": 1}));
    }
    let value = load(path)?;
    if value.get("schema_version").and_then(Value::as_i64) != Some(1) {
        return Err(Failure::new(
            format!("{}: expected schema_version 1", path.display()),
            FIX,
        ));
    }
    Ok(value)
}

pub fn write_text(path: &Path, text: &str) -> Result<(), Failure> {
    let mode = fs::metadata(path).ok().map(|m| m.permissions());
    fileio::atomic_write(path, text, false)
        .and_then(|()| {
            if let Some(mode) = mode {
                fs::set_permissions(path, mode)?;
            }
            Ok(())
        })
        .map_err(|e| Failure::new(format!("cannot write {}: {e}", path.display()), FIX))
}

pub fn write(path: &Path, value: &Value, cursor: bool) -> Result<(), Failure> {
    let text = pretty(value);
    write_text(
        path,
        &if cursor {
            text.replace('/', "\\/")
        } else {
            text
        },
    )
}

pub fn pretty(value: &Value) -> String {
    use std::fmt::Write;
    let mut out = String::new();
    for character in serde_json::to_string_pretty(value)
        .expect("JSON value")
        .chars()
    {
        if character.is_ascii() {
            out.push(character);
        } else {
            for unit in character.encode_utf16(&mut [0; 2]) {
                write!(out, "\\u{unit:04x}").expect("String write");
            }
        }
    }
    out.push('\n');
    out
}
