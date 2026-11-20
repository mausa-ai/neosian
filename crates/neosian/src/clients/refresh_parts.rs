//! One registration, read as `status` reads it. Its command line is kept word
//! for word; only what the release owns around it is regenerated (§40.48).

use super::{
    context::Context,
    diagnostics,
    files::{self, Failure},
    hooks, mcp,
    targets::{self, SERVER},
};
use serde_json::{Value, json};
use std::{collections::BTreeSet, path::PathBuf};

const STORE: [&str; 3] = ["--root", "--url", "--profile"];
const MCP: [&str; 6] = [
    "--root",
    "--url",
    "--profile",
    "--mount",
    "--actor",
    "--schema",
];
const RECORD: [&str; 8] = [
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
            executable: None,
        }
    }

    pub(super) fn left_alone(mut self, reason: impl Into<String>) -> Self {
        self.action = "left_alone";
        self.reason = Some(reason.into());
        self
    }

    pub(super) fn update(mut self, change: Change) -> Self {
        self.action = "update";
        self.change = Some(change);
        self
    }

    pub fn report(&self) -> Value {
        json!({"kind": self.kind, "path": self.path, "action": self.action,
            "reason": self.reason, "note": self.note})
    }
}

/// A command line this release writes, whose executable is the running binary.
fn recognized(
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

pub fn mcp(client: &str, context: &Context) -> Result<Option<Part>, Failure> {
    let target = targets::mcp(client, context, "user");
    let Some(argv) = targets::registered(&target) else {
        return Ok(None);
    };
    let mut part = Part::new("mcp", target.path.clone());
    if let Err(reason) = recognized(&argv, "mcp", &MCP, context) {
        return Ok(Some(part.left_alone(reason)));
    }
    part.executable = Some(argv[0].clone());
    let existing = files::read(&target.path).map_or(Value::Null, |d| d[target.key][SERVER].clone());
    let mut entry = mcp::entry(client, &argv, &[]);
    if entry.get("enabled").is_some() && existing["enabled"].is_boolean() {
        entry["enabled"] = existing["enabled"].clone();
    }
    let fields = entry.as_object().expect("an entry is an object");
    if ["command", "args"].iter().any(|key| {
        fields
            .get(*key)
            .is_some_and(|v| existing.get(*key) != Some(v))
    }) {
        return Ok(Some(part.left_alone(
            "the registered command could not be read back word for word",
        )));
    }
    if fields
        .iter()
        .all(|(key, value)| existing.get(key) == Some(value))
    {
        return Ok(Some(part));
    }
    if target.cli.is_some() {
        return Ok(Some(part.left_alone(format!(
            "{}'s own CLI writes this entry; run setup --client {client} --write to rewrite it",
            target.label
        ))));
    }
    let mut document = if client == "muse-code" {
        files::settings(&target.path)?
    } else {
        files::load(&target.path)?
    };
    for (key, value) in fields {
        document[target.key][SERVER][key] = value.clone();
    }
    Ok(Some(part.update(Change::Json(document, false))))
}

/// The event set this release writes, each event carrying exactly our one group.
fn current(document: &Value, fragment: &Value, cursor: bool) -> bool {
    let Some(present) = document["hooks"].as_object() else {
        return false;
    };
    let wanted = fragment["hooks"].as_object().expect("fragment");
    let ours = |event: &str| -> Vec<&Value> {
        present
            .get(event)
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter(|group| hooks::commands(group).into_iter().any(hooks::carries))
            .collect()
    };
    (!cursor || document["version"] == 1)
        && wanted
            .iter()
            .all(|(event, groups)| ours(event).into_iter().eq(groups.as_array().unwrap()))
        && present
            .keys()
            .filter(|event| !wanted.contains_key(*event))
            .all(|event| ours(event).is_empty())
}

pub fn hooks(client: &str, context: &Context) -> Result<Option<Part>, Failure> {
    let carrying: Vec<_> = targets::active(client, context, "user")
        .into_iter()
        .filter_map(|target| hooks::installed(&target).map(|argv| (target, argv)))
        .collect();
    let Some((target, argv)) = carrying.first() else {
        return Ok(None);
    };
    let mut part = Part::new("hooks", target.path.clone());
    if carrying.len() > 1 {
        return Ok(Some(part.left_alone(
            "the hooks are present in more than one file; setup --write keeps one",
        )));
    }
    if let Err(reason) = recognized(argv, "record", &RECORD, context) {
        return Ok(Some(part.left_alone(reason)));
    }
    part.executable = Some(argv[0].clone());
    if target.plugin {
        let text = hooks::adapter(client, argv);
        let existing = std::fs::read_to_string(&target.path)?;
        return Ok(Some(if text == existing {
            part
        } else {
            part.update(Change::Text(text))
        }));
    }
    let document = if client == "muse-code" && target.path == target.evidence.join("settings.json")
    {
        files::settings(&target.path)?
    } else {
        files::load(&target.path)?
    };
    let commands: BTreeSet<&str> = document["hooks"]
        .as_object()
        .into_iter()
        .flat_map(|events| events.values())
        .filter_map(Value::as_array)
        .flatten()
        .flat_map(hooks::commands)
        .filter(|command| hooks::carries(command))
        .collect();
    let [command] = commands.into_iter().collect::<Vec<_>>()[..] else {
        return Ok(Some(
            part.left_alone("the hook commands differ between events"),
        ));
    };
    let cursor = client == "cursor";
    if current(&document, &hooks::fragment(command, cursor), cursor) {
        return Ok(Some(part));
    }
    let updated = if cursor {
        hooks::cursor_document(&target.path, command)?
    } else {
        hooks::merge(
            document.clone(),
            &hooks::fragment(command, false),
            &target.path,
        )?
    };
    part.note.clone_from(&target.trust);
    Ok(Some(part.update(Change::Json(updated, cursor))))
}
