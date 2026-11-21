//! MCP entries and level displacement (`mcp/install.py`).

use super::{
    context::{Context, shell_join},
    files::{self, FIX, Failure},
    naming::NEW,
    receipt,
    settings::Settings,
    targets::{self, Target},
};
use crate::pyfmt;
use serde_json::{Value, json};

pub fn install(target: &Target, context: &Context, settings: &Settings, write: bool) -> Value {
    perform(target, context, settings, write).unwrap_or_else(|e| e.payload(target))
}

pub fn apply_argv(target: &Target, entry: &Value, key: &str) -> Vec<String> {
    if target.cli == Some("claude") {
        ["claude", "mcp", "add-json", "--scope", "user", key]
            .map(str::to_owned)
            .into_iter()
            .chain([pyfmt::dumps_spaced(entry, true, false)])
            .collect()
    } else {
        let mut argv = ["codex", "mcp", "add", key, "--"]
            .map(str::to_owned)
            .to_vec();
        argv.push(entry["command"].as_str().unwrap().into());
        argv.extend(
            entry["args"]
                .as_array()
                .unwrap()
                .iter()
                .map(|a| a.as_str().unwrap().into()),
        );
        argv
    }
}

fn merge(mut document: Value, target: &Target, entry: &Value, key: &str) -> Result<Value, Failure> {
    if document.get(target.key).is_none() {
        document[target.key] = json!({});
    }
    if !document[target.key].is_object() {
        return Err(Failure::new(
            format!(
                "{} in {} is not a JSON object; refusing to rewrite it",
                target.key,
                target.path.display()
            ),
            FIX,
        ));
    }
    document[target.key][key] = entry.clone();
    Ok(document)
}

pub fn displace(target: &Target, write: bool) -> Result<Option<String>, Failure> {
    let Some((key, _)) = targets::registration(target) else {
        return Ok(None);
    };
    if write {
        let mut document = files::load(&target.path)?;
        document[target.key].as_object_mut().unwrap().remove(key);
        files::write(&target.path, &document, false)?;
    }
    Ok(Some(target.path.display().to_string()))
}

/// The client's entry for a full registered command line (`argv[0]` is the executable).
pub fn entry(client: &str, argv: &[String], credentials: &[String]) -> Value {
    if client == "opencode" {
        return json!({"type": "local", "command": argv, "enabled": true});
    }
    let mut entry = json!({"command": argv[0], "args": argv[1..]});
    if ["muse-code", "cursor"].contains(&client) && !credentials.is_empty() {
        let prefix = if client == "cursor" { "env:" } else { "" };
        let mut names = json!({});
        for name in credentials {
            names[name.as_str()] = json!(format!("${{{prefix}{name}}}"));
        }
        entry["env"] = names;
    }
    if client == "pi" {
        entry["exposure"] = json!("direct");
    }
    entry
}

fn perform(
    target: &Target,
    context: &Context,
    settings: &Settings,
    write: bool,
) -> Result<Value, Failure> {
    let mut argv = vec![context.executable.clone(), "mcp".into()];
    argv.extend(settings.store.clone());
    argv.extend(settings.mounts.clone());
    argv.extend([
        "--actor".into(),
        settings
            .actor
            .clone()
            .unwrap_or_else(|| format!("mcp:{}", target.client)),
    ]);
    if let Some(schema) = &settings.schema {
        argv.extend(["--schema".into(), schema.clone()]);
    }
    let entry = entry(&target.client, &argv, &settings.credentials);
    files::evidence(target)?;
    let key = super::write_key::write_key(target, context)?;
    if write && target.cli.is_some() {
        return Err(Failure::new(
            format!(
                "{}'s own CLI writes {}; --write is not offered",
                target.label,
                target.path.display()
            ),
            format!(
                "apply it with: {}",
                shell_join(&apply_argv(target, &entry, key))
            ),
        ));
    }
    let project = targets::mcp(&target.client, context, "project");
    let mut shadowed = None;
    let mut displaced = None;
    let mut merged = None;
    if target.client == "muse-code" {
        let document = if target.level == "user" {
            files::settings(&target.path)?
        } else {
            files::load(&target.path)?
        };
        if document.get("mcp_servers").is_some() {
            return Err(Failure::new(
                "Muse settings use legacy mcp_servers",
                "rename it to mcpServers before installing",
            ));
        }
        merged = Some(merge(document, target, &entry, key)?);
        if target.level == "user" && targets::registered(&project).is_some() {
            shadowed = Some(project.path.display().to_string());
        }
    } else if ["cursor", "pi"].contains(&target.client.as_str()) {
        merged = Some(merge(files::load(&target.path)?, target, &entry, key)?);
        if target.client == "pi" && target.level == "user" {
            displaced = displace(&project, write)?;
        }
    } else if target.level == "user" && project.path != target.path {
        displaced = displace(&project, write && target.cli.is_none())?;
    }
    let created = write && !target.path.exists();
    if write {
        let merged = match merged {
            Some(v) => v,
            None => merge(files::load(&target.path)?, target, &entry, key)?,
        };
        files::write(&target.path, &merged, false)?;
        if key == NEW {
            receipt::record(
                &target.client,
                context,
                &target.path,
                &targets::pointer(target, NEW),
                &entry,
            )?;
        }
    }
    let apply = target
        .cli
        .map(|_| shell_join(&apply_argv(target, &entry, key)));
    let commented = target.path.with_extension("jsonc");
    let note = if target.client == "opencode" && target.path != commented {
        std::fs::read_to_string(&commented).ok().map(|text| {
            let mut note = format!("{} beside it carries comments, so the entry lands in {}; OpenCode reads both and merges them, the .jsonc last", commented.file_name().unwrap().to_string_lossy(), target.path.file_name().unwrap().to_string_lossy());
            if text.contains(key) { note.push_str(&format!(" (it names {key} itself: that entry wins; remove it)")); }
            note
        })
    } else {
        None
    };
    Ok(
        json!({"success": true, "client": target.client, "label": target.label, "level": target.level,
        "config_path": target.path, "servers_key": target.key, "server_name": key, "entry": entry,
        "displaced": displaced, "mcp_shadowed_by": shadowed, "written": write, "created": created, "apply": apply, "note": note}),
    )
}
