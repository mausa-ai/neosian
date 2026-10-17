//! `list_skills` and `load_skill` (`memory/skills.py`, DESIGN §24.3): the
//! mounts' `skills/` documents and the wheel's own, read live per call;
//! a skill in a mount names its document and version, so a revision is
//! one `str_replace` away.

use serde_json::{Map, Value, json};

use super::{Outcome, Tool, text};
use crate::assets::{TOOLS, literal, mapping, quoted};
use crate::conversation::Error;
use crate::mcp::schema::{Field, Kind, Presence};
use crate::mcp::server::Server;
use crate::memory::dispatch::corrective;
use crate::memory::skill::{SkillEntry, list_skills, load_skill};
use crate::pyfmt::py_str;

const LOAD_FIELDS: [Field; 1] = [Field {
    name: "name",
    kind: Kind::Str,
    presence: Presence::Required,
}];

pub fn list() -> Tool {
    Tool {
        name: "list_skills",
        description: quoted(TOOLS, "skill_list").expect("tools.yaml carries skill_list"),
        params: Vec::new(),
        fields: &[],
        read_only: true,
        destructive: false,
        call: list_call,
    }
}

pub fn load() -> Tool {
    Tool {
        name: "load_skill",
        description: quoted(TOOLS, "skill_load").expect("tools.yaml carries skill_load"),
        params: mapping(TOOLS, "skill_load_params").expect("tools.yaml carries the params"),
        fields: &LOAD_FIELDS,
        read_only: true,
        destructive: false,
        call: load_call,
    }
}

fn row(entry: &SkillEntry) -> Value {
    let mut row = Map::new();
    row.insert("name".to_string(), json!(entry.name));
    match &entry.skill {
        Some(skill) => row.insert("description".to_string(), json!(skill.description)),
        None => row.insert("error".to_string(), json!(entry.error)),
    };
    if let Some(path) = &entry.path {
        row.insert("path".to_string(), json!(path));
        row.insert("version".to_string(), json!(entry.version));
    }
    Value::Object(row)
}

fn list_call(server: &Server, _: &Map<String, Value>) -> Outcome {
    let memory = &server.memory;
    let entries = match list_skills(&memory.store, &memory.mounts, &server.shipped) {
        Ok(entries) => entries,
        Err(error) => return Outcome::failed("list_skills", &error.text()),
    };
    let rows: Vec<Value> = entries.iter().map(row).collect();
    let outcome = Outcome::ok(py_str(&Value::Array(rows)));
    if memory.mounts.iter().any(|mount| !mount.read_only) {
        outcome.hint(literal(TOOLS, "skill_guide").expect("tools.yaml carries the guide"))
    } else {
        outcome
    }
}

fn load_call(server: &Server, arguments: &Map<String, Value>) -> Outcome {
    let memory = &server.memory;
    let name = text(arguments, "name").unwrap_or_default();
    let entry = match load_skill(&memory.store, &memory.mounts, &server.shipped, name) {
        Ok(entry) => entry,
        Err(Error::Memory(error)) => return corrective(&error).into(),
        Err(error) => return Outcome::failed("load_skill", &error.text()),
    };
    let Some(entry) = entry else {
        let names = match list_skills(&memory.store, &memory.mounts, &server.shipped) {
            Ok(entries) => entries
                .iter()
                .map(|entry| entry.name.clone())
                .collect::<Vec<_>>(),
            Err(error) => return Outcome::failed("load_skill", &error.text()),
        };
        return Outcome::fail(format!("Skill '{name}' not found"))
            .hint(format!("Available skills: {}", names.join(", ")));
    };
    let Some(skill) = &entry.skill else {
        let version = entry.version.map_or("None".to_string(), |v| v.to_string());
        return Outcome::fail(entry.error.clone().unwrap_or_default()).hint(format!(
            "Fix {} (version {version}) with the memory tool's str_replace.",
            entry.path.as_deref().unwrap_or("None")
        ));
    };
    let outcome = Outcome::ok(skill.content.clone());
    match (&entry.path, entry.version) {
        (Some(path), Some(version)) => outcome.hint(format!("{path} (version {version})")),
        _ => outcome,
    }
}
