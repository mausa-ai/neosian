//! Pasteable standalone artifacts and their guidance (`mcp/install.py`, `record/install.py`).

use super::{
    files,
    settings::Settings,
    targets::{SERVER, Target},
};
use crate::pyfmt;
use serde_json::{Value, json};
use std::io::{self, Write};

fn note(target: &Target, record: bool) -> String {
    let user = target.level == "user";
    let level = &target.level;
    match (target.client.as_str(), record) {
        ("cursor", true) => format!("{level} level: Cursor's native hooks"),
        ("pi", true) => format!("{level} level: Pi's session extension (Pi 1.0.1 or later)"),
        ("muse-code", _) if !user => "project level: trusted Muse workspace".into(),
        ("pi", false) if !user => "project level: trusted Pi workspace".into(),
        ("claude-desktop", _) => "user level: every Claude Desktop conversation".into(),
        ("opencode", false) => format!(
            "{} (OpenCode reads opencode.json and opencode.jsonc and merges them, the .jsonc last)",
            if user {
                "user level: every OpenCode project on this machine"
            } else {
                "project level: the config travels with this directory's repo"
            }
        ),
        _ if !user => format!(
            "project level: {} travels with this directory's repo",
            if !record {
                ".mcp.json"
            } else {
                match target.client.as_str() {
                    "claude-code" => ".claude/settings.json",
                    "codex" => ".codex/hooks.json",
                    "opencode" => ".opencode/plugins/neosian-record.js",
                    _ => unreachable!(),
                }
            }
        ),
        _ => {
            let noun = if record || ["muse-code", "pi"].contains(&target.client.as_str()) {
                "session"
            } else {
                "project"
            };
            let suffix = match target.cli {
                Some("claude") => "; Claude Code's own CLI writes this file",
                Some("codex") => "; Codex's own CLI writes its TOML",
                _ => "",
            };
            format!(
                "user level: every {} {noun} on this machine{suffix}",
                target.label
            )
        }
    }
}

pub fn render(
    payload: &Value,
    target: &Target,
    settings: &Settings,
    record: bool,
    json: bool,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<u8> {
    let success = payload["success"] == true;
    if json {
        writeln!(out, "{payload}")?;
    }
    if !success {
        if !json {
            writeln!(
                err,
                "error: {}\nhint: {}",
                payload["error"].as_str().unwrap(),
                payload["hint"].as_str().unwrap()
            )?;
        }
        return Ok(1);
    }
    if !record && json {
        return Ok(0);
    }
    let write = payload["written"] == true;
    let path = payload["config_path"].as_str().unwrap();
    let displaced = payload["displaced"].as_str();
    let action = if payload["created"] == true {
        "created"
    } else {
        "updated"
    };
    if record && target.client == "muse-code" {
        if !json {
            if write {
                for path in payload["files"].as_object().unwrap().keys() {
                    writeln!(out, "updated {path}")?;
                }
            } else {
                write!(
                    out,
                    "{}",
                    serde_json::to_string_pretty(&payload["files"]).unwrap()
                )?;
                writeln!(out)?;
                writeln!(
                    err,
                    "Muse Code: proposed files; re-run with --write to apply"
                )?;
            }
        }
        if target.level == "project" {
            writeln!(
                err,
                "hint: Muse loads project hooks only in a trusted workspace"
            )?;
        }
        return Ok(0);
    }
    if record && target.client == "cursor" {
        if !json {
            if write {
                writeln!(out, "{action} {path}")?;
            } else {
                let fragment = json!({"version": 1, "hooks": payload["hooks"]});
                write!(out, "{}", files::pretty(&fragment).replace('/', "\\/"))?;
                writeln!(err, "Cursor: {}; target {path}", note(target, true))?;
            }
        }
        if let Some(hint) = &target.trust {
            writeln!(err, "hint: {hint}")?;
        }
        if let Some(old) = displaced {
            writeln!(
                err,
                "{} neosian hooks from {old}",
                if write { "removed" } else { "would remove" }
            )?;
        }
        return Ok(0);
    }
    if json {
        return Ok(0);
    }
    if let Some(note) = payload["note"].as_str() {
        writeln!(err, "note: {note}")?;
    }
    if let Some(shadow) = payload["mcp_shadowed_by"].as_str() {
        writeln!(
            err,
            "note: preserved shared {shadow}; it overrides Muse's user registration"
        )?;
    }
    if write {
        writeln!(out, "{action} {path}")?;
        if let Some(old) = displaced {
            writeln!(out, "removed ours from {old}")?;
        }
    } else {
        if record {
            if let Some(plugin) = payload["plugin"].as_str() {
                write!(out, "{plugin}")?;
            } else {
                write!(
                    out,
                    "{}",
                    files::pretty(&json!({"hooks": payload["hooks"]}))
                )?;
            }
        } else if target.path.extension().is_some_and(|s| s == "toml") {
            let entry = &payload["entry"];
            let args = entry["args"]
                .as_array()
                .unwrap()
                .iter()
                .map(|arg| pyfmt::dumps_spaced(arg, true, false))
                .collect::<Vec<_>>()
                .join(", ");
            writeln!(
                out,
                "[{}.{SERVER}]\ncommand = {}\nargs = [{args}]",
                target.key,
                pyfmt::dumps_spaced(&entry["command"], true, false)
            )?;
        } else {
            let mut fragment = json!({target.key: {SERVER: payload["entry"]}});
            if target.client == "muse-code" && target.level == "user" {
                fragment["schema_version"] = json!(1);
            }
            write!(out, "{}", files::pretty(&fragment))?;
        }
        writeln!(
            err,
            "{}: {}\ntarget: {path}",
            target.label,
            note(target, record)
        )?;
        if let Some(apply) = payload["apply"].as_str() {
            writeln!(err, "hint: apply it with: {apply}")?;
        } else {
            writeln!(err, "hint: re-run with --write to apply this to {path}")?;
        }
        if let Some(old) = displaced {
            if record {
                writeln!(
                    err,
                    "hint: --write also removes ours from {old}: one level per client, or every span lands twice"
                )?;
            } else {
                writeln!(
                    err,
                    "hint: this directory's {old} still carries ours and shadows the user level; applying the registration removes it"
                )?;
            }
        }
    }
    if record && let Some(hint) = &target.trust {
        writeln!(err, "hint: {hint}")?;
    }
    for name in &settings.credentials {
        let noun = if record { "hook line" } else { "registration" };
        let what = if *name == "NEOSIAN_CLIENT_TOKEN" {
            "the token is"
        } else {
            "it is"
        };
        writeln!(
            err,
            "hint: set {name} in the client's own environment — {what} deliberately never written into a {noun}"
        )?;
    }
    Ok(0)
}
