//! Key-preserving, individually atomic edits of client-owned files. A symlinked
//! file is written at its target, so the link stays; an existing mode is kept.

use std::cell::RefCell;
use std::fs;
use std::path::{Path, PathBuf};

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
    load_text(path).map(|(_, value)| value)
}

/// The document and the exact text it was parsed from (`None` when absent).
pub fn load_text(path: &Path) -> Result<(Option<String>, Value), Failure> {
    if !path.exists() {
        return Ok((None, json!({})));
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
    Ok((Some(text), value))
}

pub fn read(path: &Path) -> Option<Value> {
    let text = fs::read_to_string(path).ok()?;
    let value: Value = if path.extension().is_some_and(|s| s == "toml") {
        serde_json::to_value(text.parse::<toml::Table>().ok()?).ok()?
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

/// One write made while a caller records: the file written (a link's target),
/// what it held before (`None` when it did not exist) and what was written.
pub struct Written {
    pub path: PathBuf,
    pub prior: Option<(String, fs::Permissions)>,
    pub text: String,
}

/// What a recorded run wrote, and the directories its writes created.
#[derive(Default)]
pub struct Record {
    pub written: Vec<Written>,
    pub created: Vec<PathBuf>,
}

thread_local! {
    static RECORD: RefCell<Option<Record>> = const { RefCell::new(None) };
}

/// Run `work` and return every client file it wrote, so a caller can undo it.
pub fn recorded<T>(work: impl FnOnce() -> T) -> (T, Record) {
    RECORD.with(|record| *record.borrow_mut() = Some(Record::default()));
    let result = work();
    let record = RECORD.with(|record| record.borrow_mut().take());
    (result, record.unwrap_or_default())
}

fn recording() -> bool {
    RECORD.with(|record| record.borrow().is_some())
}

/// The file a write lands in: a symlink's target, so the link itself stays.
pub fn resolved(path: &Path) -> std::io::Result<PathBuf> {
    match fs::symlink_metadata(path) {
        Ok(found) if found.file_type().is_symlink() => fs::canonicalize(path),
        _ => Ok(path.to_path_buf()),
    }
}

/// Why an existing file must not be replaced: its owner mode does not allow writing.
pub fn read_only(path: &Path) -> Option<String> {
    let permissions = fs::metadata(path).ok()?.permissions();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = permissions.mode() & 0o7777;
        (mode & 0o200 == 0).then(|| format!("{} is read-only (mode {mode:o})", path.display()))
    }
    #[cfg(not(unix))]
    {
        permissions
            .readonly()
            .then(|| format!("{} is read-only", path.display()))
    }
}

pub fn write_text(path: &Path, text: &str) -> Result<(), Failure> {
    let cannot =
        |e: std::io::Error| Failure::new(format!("cannot write {}: {e}", path.display()), FIX);
    let target = resolved(path).map_err(cannot)?;
    let mode = fs::metadata(&target).ok().map(|m| m.permissions());
    let recording = recording();
    let prior = match &mode {
        Some(mode) if recording => {
            Some((fs::read_to_string(&target).map_err(cannot)?, mode.clone()))
        }
        _ => None,
    };
    let created: Vec<PathBuf> = if recording {
        target
            .ancestors()
            .skip(1)
            .take_while(|directory| !directory.as_os_str().is_empty() && !directory.exists())
            .map(Path::to_path_buf)
            .collect()
    } else {
        vec![]
    };
    let written = fileio::atomic_write(&target, text, false).and_then(|()| match &mode {
        Some(mode) => fs::set_permissions(&target, mode.clone()),
        None => Ok(()),
    });
    RECORD.with(|record| {
        if let Some(record) = record.borrow_mut().as_mut() {
            record
                .created
                .extend(created.into_iter().filter(|d| d.exists()));
            if written.is_ok() {
                record.written.push(Written {
                    path: target.clone(),
                    prior,
                    text: text.into(),
                });
            }
        }
    });
    written.map_err(cannot)
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
