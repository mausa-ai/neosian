//! Native Codex entry points for the live MCP workflows, with guarded ownership.

use super::{
    context::Context,
    files::{self, Failure},
    targets,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, io, path::Path};

const NAME: &str = "neosian-upkeep";
const RECEIPT: &str = "neosian-skills.json";
const ASSETS: &[(&str, &str)] = &[
    (
        "SKILL.md",
        include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/clients/codex/skills/neosian-upkeep/SKILL.md"
        )),
    ),
    (
        "agents/openai.yaml",
        include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/clients/codex/skills/neosian-upkeep/agents/openai.yaml"
        )),
    ),
];

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Receipt {
    format: u8,
    enabled: bool,
    files: BTreeMap<String, String>,
}

use crate::knowledge::model::digest;

fn refusal(path: &Path, reason: &str) -> Failure {
    Failure::new(
        format!(
            "{}: {reason}; refusing to replace or remove it",
            path.display()
        ),
        "Preserve your files and move the conflicting entry before repeating setup",
    )
}

fn metadata(path: &Path, directory: bool) -> Result<bool, Failure> {
    match fs::symlink_metadata(path) {
        Ok(value)
            if !value.is_symlink()
                && if directory {
                    value.is_dir()
                } else {
                    value.is_file()
                } =>
        {
            Ok(true)
        }
        Ok(_) => Err(refusal(
            path,
            "expected an ordinary file or directory, not a symlink",
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error.into()),
    }
}

fn receipt(path: &Path) -> Result<Option<Receipt>, Failure> {
    if !metadata(path, false)? {
        return Ok(None);
    }
    let saved: Receipt = serde_json::from_slice(&fs::read(path)?)
        .map_err(|_| refusal(path, "invalid native skill receipt"))?;
    if saved.format != 1
        || saved.files.len() != ASSETS.len()
        || ASSETS.iter().any(|(name, _)| {
            !saved
                .files
                .get(*name)
                .is_some_and(|hash| hash.len() == 64 && hash.bytes().all(|b| b.is_ascii_hexdigit()))
        })
    {
        return Err(refusal(path, "unsupported native skill receipt"));
    }
    Ok(Some(saved))
}

fn remove_empty(path: &Path) -> Result<(), Failure> {
    match fs::remove_dir(path) {
        Ok(()) => Ok(()),
        Err(error)
            if matches!(
                error.kind(),
                io::ErrorKind::NotFound | io::ErrorKind::DirectoryNotEmpty
            ) =>
        {
            Ok(())
        }
        Err(error) => Err(error.into()),
    }
}

pub fn install(context: &Context, mode: &str, write: bool) -> Value {
    let mut target = targets::mcp("codex", context, "user");
    target.path = context
        .directory("codex")
        .join("skills")
        .join(NAME)
        .join("SKILL.md");
    apply(context, mode, write).unwrap_or_else(|error| error.payload(&target))
}

fn apply(context: &Context, mode: &str, write: bool) -> Result<Value, Failure> {
    files::evidence(&targets::mcp("codex", context, "user"))?;
    let base = context.directory("codex");
    let root = base.join("skills").join(NAME);
    let saved_path = base.join(RECEIPT);
    let saved = receipt(&saved_path)?;
    metadata(&base.join("skills"), true)?;
    let exists = metadata(&root, true)?;
    metadata(&root.join("agents"), true)?;
    if exists && saved.is_none() {
        return Err(refusal(&root, "skill is not managed by neosian"));
    }
    let mut installed = false;
    let mut current = true;
    for (name, body) in ASSETS {
        let path = root.join(name);
        if metadata(&path, false)? {
            installed = true;
            let hash = digest(&fs::read(&path)?);
            let matches = hash == digest(body.as_bytes());
            // Accept the previous or new bytes so an interrupted update can resume.
            if !matches && saved.as_ref().and_then(|s| s.files.get(*name)) != Some(&hash) {
                return Err(refusal(&path, "managed skill has local edits"));
            }
            current &= matches;
        } else {
            current = false;
        }
    }
    let enabled = match mode {
        "on" => true,
        "off" => false,
        _ => saved.as_ref().is_none_or(|s| s.enabled),
    };
    let action = match (enabled, installed, current) {
        (false, true, _) => "remove",
        (false, false, _) => "keep_disabled",
        (true, _, true) => "unchanged",
        (true, true, false) => "update",
        (true, false, false) => "install",
    };
    let mut report = json!({"success":true,"client":"codex","config_path":root.join("SKILL.md"),
        "receipt_path":saved_path,"name":NAME,"enabled":enabled,"installed":installed,
        "current":current,"action":action,"written":write,"applied":write,
        "discovery":"not_observed"});
    if !write {
        return Ok(report);
    }
    let next = Receipt {
        format: 1,
        enabled,
        files: ASSETS
            .iter()
            .map(|(name, body)| ((*name).into(), digest(body.as_bytes())))
            .collect(),
    };
    if saved.is_none() {
        // Record ownership before creating files, allowing interrupted installs to resume.
        files::write(&saved_path, &serde_json::to_value(&next).unwrap(), false)?;
    }
    if enabled {
        for (name, body) in ASSETS {
            files::write_text(&root.join(name), body)?;
        }
    } else {
        // Retain old hashes until removal finishes, including across interrupted upgrades.
        let disabled = Receipt {
            enabled: false,
            files: saved
                .as_ref()
                .map_or_else(|| next.files.clone(), |s| s.files.clone()),
            format: 1,
        };
        files::write(&saved_path, &serde_json::to_value(disabled).unwrap(), false)?;
        for (name, _) in ASSETS {
            let path = root.join(name);
            if path.exists() {
                fs::remove_file(path)?;
            }
        }
        remove_empty(&root.join("agents"))?;
        remove_empty(&root)?;
    }
    files::write(&saved_path, &serde_json::to_value(next).unwrap(), false)?;
    report["installed"] = json!(enabled);
    report["current"] = json!(enabled);
    Ok(report)
}

pub fn status(context: &Context) -> Value {
    install(context, "auto", false)
}

pub fn needs_setup(report: &Value) -> bool {
    report["success"] != true
        || if report["enabled"] == false {
            report["installed"] == true
        } else {
            report["current"] != true
        }
}
