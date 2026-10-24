//! Manage ordinary mounted skill documents; the host agent executes instructions.

pub mod cli;
pub mod model;
mod package;
mod profile;
mod revision;
pub mod runtime;

use serde_json::{Value, json};

use crate::knowledge::model::{MAX_BODY, invalid};
use crate::memory::dispatch::Memory;
use crate::memory::mounts::resolve;
use crate::memory::skill::{SkillEntry, list_skills, load_skill, shipped_skills};
use crate::store::{Document, error::Error};

fn current(memory: &Memory, path: &str) -> Result<Document, Error> {
    let (mount, document) = resolve(&memory.mounts, path)?;
    let doc = memory
        .store
        .read(&mount.scope, document)?
        .filter(|d| !d.envelope.redacted)
        .ok_or_else(|| invalid(format!("document is missing or redacted: {path}")))?;
    if doc.content.len() > MAX_BODY {
        return Err(invalid("skill document exceeds 1 MiB"));
    }
    Ok(doc)
}

pub fn row(entry: &SkillEntry, body: bool) -> Value {
    let mut row = json!({"name":entry.name,"path":entry.path,"version":entry.version,
        "description":entry.skill.as_ref().map(|s| &s.description),"error":entry.error,"details":entry.details});
    if body {
        row["content"] = json!(entry.skill.as_ref().map(|s| &s.content));
    }
    row
}

fn discover(memory: &Memory) -> Result<(Value, u8), Error> {
    let entries = list_skills(memory.store.as_ref(), &memory.mounts, &shipped_skills())
        .map_err(|e| invalid(e.text()))?;
    Ok((
        json!({"skills":entries.iter().map(|e| row(e, false)).collect::<Vec<_>>()}),
        0,
    ))
}

fn load(memory: &Memory, name: &str) -> Result<(Value, u8), Error> {
    let entry = load_skill(
        memory.store.as_ref(),
        &memory.mounts,
        &shipped_skills(),
        name,
    )
    .map_err(|e| invalid(e.text()))?
    .ok_or_else(|| invalid("skill is unavailable or not selected"))?;
    let code = u8::from(entry.skill.is_none());
    Ok((row(&entry, true), code))
}

fn inspect(memory: &Memory, path: &str, validate: bool) -> Result<(Value, u8), Error> {
    model::address(path)?;
    let doc = current(memory, path)?;
    let checked = runtime::check(
        memory.store.as_ref(),
        &memory.mounts,
        path,
        &doc.content,
        doc.envelope.version,
        false,
        &Default::default(),
    );
    let mut report = runtime::provenance(
        memory.store.as_ref(),
        &memory.mounts,
        path,
        doc.envelope.version,
        &doc.content,
    )?;
    let code = match checked {
        Ok(details) => {
            report["details"] = details;
            0
        }
        Err(error) => {
            report["error"] = json!(crate::conversation::Error::from(error).text());
            1
        }
    };
    report["success"] = json!(code == 0);
    if !validate {
        report["content"] = json!(doc.content);
    }
    Ok((report, code))
}

#[cfg(test)]
mod tests;
