//! One registration, read as `status` reads it. Its command line is kept word
//! for word; the refresh changes only what the release owns and needs to change,
//! and leaves a part alone, with its reason, when it cannot do that (§40.48).

use super::{
    context::Context,
    diagnostics,
    files::{self, Failure},
    mcp,
    targets::{self, SERVER},
};
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    path::{Path, PathBuf},
};

/// How every reason found before writing ends: the retry after the fix.
pub const RETRY: &str = "then run neosian setup --refresh";
const STORE: [&str; 3] = ["--root", "--url", "--profile"];
const MCP: [&str; 6] = [
    "--root",
    "--url",
    "--profile",
    "--mount",
    "--actor",
    "--schema",
];
pub(super) const RECORD: [&str; 8] = [
    "--root",
    "--url",
    "--profile",
    "--mount",
    "--schema",
    "--agent",
    "--spool",
    "--project",
];

pub enum Change {
    Json(Value, bool),
    Text(String),
    Skills,
    Cache(String),
}

pub struct Part {
    pub kind: &'static str,
    pub path: PathBuf,
    pub action: &'static str,
    pub reason: Option<String>,
    pub note: Option<String>,
    pub change: Option<Change>,
    /// The text a change was computed from; another text at write time stops it.
    pub read: Option<String>,
    /// The registered executable, set once it is known to run this installation.
    pub executable: Option<String>,
}

impl Part {
    pub(super) fn new(kind: &'static str, path: PathBuf) -> Self {
        Self {
            kind,
            path,
            action: "unchanged",
            reason: None,
            note: None,
            change: None,
            read: None,
            executable: None,
        }
    }

    pub(super) fn left_alone(mut self, reason: impl Into<String>) -> Self {
        self.action = "left_alone";
        self.reason = Some(reason.into());
        self.change = None;
        self
    }

    pub(super) fn off(mut self) -> Self {
        self.action = "off";
        self
    }

    pub(super) fn update(mut self, change: Change, read: Option<String>) -> Self {
        self.action = "update";
        self.change = Some(change);
        self.read = read;
        self
    }

    /// A file this change would replace must be writable by its owner.
    pub(super) fn guarded(self, paths: &[PathBuf]) -> Self {
        match paths.iter().find_map(|path| files::read_only(path)) {
            Some(why) => self.left_alone(format!("{why}; it is left as it is")),
            None => self,
        }
    }

    pub(super) fn refused(self, failure: &Failure) -> Self {
        self.left_alone(format!("{}; fix it, {RETRY}", failure.message))
    }

    pub fn report(&self) -> Value {
        json!({"kind": self.kind, "path": self.path, "action": self.action,
            "reason": self.reason, "note": self.note})
    }
}

/// A command line this release writes, whose executable is the running binary.
pub(super) fn recognized(
    argv: &[String],
    verb: &str,
    flags: &[&str],
    context: &Context,
) -> Result<(), String> {
    let command = argv.first().map_or("", String::as_str);
    let executable = diagnostics::executable(Some(command), context);
    if executable["resolved"].is_null() {
        return Err(format!(
            "the registered executable does not resolve: {command}"
        ));
    }
    if executable["matches_selected"] != true {
        return Err(format!(
            "the registered executable is another installation: {command}"
        ));
    }
    let Some(words) = argv.get(2..).filter(|_| argv[1] == verb) else {
        return Err(format!("the registered command is not neosian {verb}"));
    };
    let mut seen = BTreeSet::new();
    for pair in words.chunks(2) {
        let [flag, value] = pair else {
            return Err(format!("{} has no value", pair[0]));
        };
        let repeated = flag != "--mount" && !seen.insert(flag.as_str());
        if !flags.contains(&flag.as_str()) || value.is_empty() || repeated {
            return Err(format!("{flag} is not written that way by this release"));
        }
    }
    if seen.iter().filter(|flag| STORE.contains(flag)).count() > 1 {
        return Err("more than one store is selected".into());
    }
    Ok(())
}

/// The document and its exact text; Muse settings must declare schema 1.
pub(super) fn load(path: &Path, settings: bool) -> Result<(Option<String>, Value), Failure> {
    let (text, value) = files::load_text(path)?;
    if settings && value.get("schema_version").and_then(Value::as_i64) != Some(1) {
        let message = format!("{}: expected schema_version 1", path.display());
        return Err(Failure::new(message, files::FIX));
    }
    Ok((text, value))
}

pub fn mcp(client: &str, context: &Context) -> Option<Part> {
    let target = targets::mcp(client, context, "user");
    let argv = targets::registered(&target)?;
    let mut part = Part::new("mcp", target.path.clone());
    if let Err(reason) = recognized(&argv, "mcp", &MCP, context) {
        return Some(part.left_alone(reason));
    }
    part.executable = Some(argv[0].clone());
    let existing = files::read(&target.path).map_or(Value::Null, |d| d[target.key][SERVER].clone());
    let entry = mcp::entry(client, &argv, &[]);
    let fields = entry.as_object().expect("an entry is an object");
    if ["command", "args"].iter().any(|key| {
        fields
            .get(*key)
            .is_some_and(|v| existing.get(*key) != Some(v))
    }) {
        return Some(
            part.left_alone("the registered command could not be read back word for word"),
        );
    }
    // Only members this release writes and the entry lacks are added: a value
    // present may be a person's or the client's choice, and OpenCode's `enabled`
    // switch is never added.
    let missing: Vec<_> = fields
        .iter()
        .filter(|(key, _)| *key != "enabled" && existing.get(*key).is_none())
        .collect();
    if missing.is_empty() {
        return Some(part);
    }
    if target.cli.is_some() {
        return Some(part.left_alone(format!(
            "{}'s own CLI writes this entry, and this release does not rewrite it",
            target.label
        )));
    }
    let (read, mut document) = match load(&target.path, client == "muse-code") {
        Ok(loaded) => loaded,
        Err(failure) => return Some(part.refused(&failure)),
    };
    for (key, value) in missing {
        document[target.key][SERVER][key] = value.clone();
    }
    Some(
        part.update(Change::Json(document, false), read)
            .guarded(&[target.path]),
    )
}
