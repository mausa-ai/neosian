//! Hook recognition and installation (`record/install.py`, `targets.py`).

use std::fs;
use std::path::Path;
use std::sync::LazyLock;

use fancy_regex::Regex;
use serde_json::{Value, json};

use super::{
    context::{Context, shell_join},
    files::{self, FIX, Failure},
    settings::Settings,
    targets::{self, Target},
};
use crate::pyfmt;

const EVENTS: [&str; 4] = ["UserPromptSubmit", "PostToolUse", "Stop", "SessionStart"];
const CURSOR: [&str; 6] = [
    "sessionStart",
    "beforeSubmitPrompt",
    "postToolUse",
    "afterMCPExecution",
    "afterAgentResponse",
    "stop",
];
static VERB: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?:^|\s)\S*neosian(?:\.exe)?\s+record(?:\s|$)").unwrap());
static PLUGIN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r#"\[[^\[\]]*(?:"-m", "neosian\.record"|"[^"]*neosian(?:\.exe)?", "record")[^\[\]]*\]"#,
    )
    .unwrap()
});

fn carries(command: &str) -> bool {
    command.contains("-m neosian.record") || VERB.is_match(command).unwrap_or(false)
}

fn commands(group: &Value) -> Vec<&str> {
    let mut commands = Vec::new();
    if let Some(command) = group.get("command").and_then(Value::as_str) {
        commands.push(command);
    }
    if let Some(hooks) = group.get("hooks").and_then(Value::as_array) {
        commands.extend(
            hooks
                .iter()
                .filter_map(|h| h.get("command").and_then(Value::as_str)),
        );
    }
    commands
}

pub fn installed(target: &Target) -> Option<Vec<String>> {
    if target.plugin {
        let text = fs::read_to_string(&target.path).ok()?;
        let found = PLUGIN.find(&text).ok()??;
        return serde_json::from_str(found.as_str()).ok();
    }
    let document = files::read(&target.path)?;
    for groups in document
        .get("hooks")?
        .as_object()?
        .values()
        .filter_map(Value::as_array)
    {
        for group in groups {
            if let Some(command) = commands(group).into_iter().find(|s| carries(s)) {
                return shlex::split(command);
            }
        }
    }
    None
}

pub fn fragment(command: &str, cursor: bool) -> Value {
    let mut hooks = json!({});
    for event in if cursor {
        CURSOR.as_slice()
    } else {
        EVENTS.as_slice()
    } {
        hooks[*event] = if cursor {
            json!([{"command": command}])
        } else {
            json!([{"hooks": [{"type": "command", "command": command}]}])
        };
    }
    if cursor {
        json!({"version": 1, "hooks": hooks})
    } else {
        json!({"hooks": hooks})
    }
}

pub fn merge(mut document: Value, fragment: &Value, path: &Path) -> Result<Value, Failure> {
    if document.get("hooks").is_none() {
        document["hooks"] = json!({});
    }
    let hooks = document["hooks"].as_object_mut().ok_or_else(|| {
        Failure::new(
            format!(
                "hooks in {} is not a JSON object; refusing to rewrite it",
                path.display()
            ),
            FIX,
        )
    })?;
    for (event, groups) in fragment["hooks"].as_object().expect("fragment") {
        let current = hooks.entry(event).or_insert_with(|| json!([]));
        let current = current.as_array_mut().ok_or_else(|| {
            Failure::new(
                format!(
                    "hooks.{event} in {} is not a JSON array; refusing to rewrite it",
                    path.display()
                ),
                FIX,
            )
        })?;
        current.retain(|group| !commands(group).into_iter().any(carries));
        current.extend(groups.as_array().expect("fragment groups").iter().cloned());
    }
    Ok(document)
}

pub fn strip(mut document: Value) -> Value {
    if let Some(hooks) = document.get_mut("hooks").and_then(Value::as_object_mut) {
        hooks.retain(|_, groups| {
            if let Some(groups) = groups.as_array_mut() {
                groups.retain(|g| !commands(g).into_iter().any(carries));
                !groups.is_empty()
            } else {
                true
            }
        });
    }
    document
}

fn cursor_document(path: &Path, command: &str) -> Result<Value, Failure> {
    let document = files::load(path)?;
    if path.exists() && document.get("version").and_then(Value::as_i64) != Some(1) {
        return Err(Failure::new(
            format!("{} requires Cursor hooks version 1", path.display()),
            FIX,
        ));
    }
    if let Some(hooks) = document.get("hooks") {
        let valid = hooks.as_object().is_some_and(|events| {
            events.values().all(|groups| {
                groups.as_array().is_some_and(|gs| {
                    gs.iter().all(|g| {
                        g.get("command").is_some_and(Value::is_string)
                            || (g.get("type").and_then(Value::as_str) == Some("prompt")
                                && g.get("prompt").is_some_and(Value::is_string))
                    })
                })
            })
        });
        if !valid {
            return Err(Failure::new(
                format!("invalid Cursor hooks in {}", path.display()),
                FIX,
            ));
        }
    }
    let mut document = merge(document, &fragment(command, true), path)?;
    document["version"] = json!(1);
    Ok(document)
}

pub fn install(target: &Target, context: &Context, settings: &Settings, write: bool) -> Value {
    let mut argv = vec![context.executable.clone(), "record".into()];
    argv.extend(settings.store.clone());
    argv.extend(settings.mounts.clone());
    if target.client != "claude-code" {
        argv.extend(["--agent".into(), target.client.clone()]);
    }
    argv.extend(["--spool".into(), settings.spool.display().to_string()]);
    let mut command = shell_join(&argv);
    if target.client == "claude-code" && target.level == "user" && settings.mounts.is_empty() {
        command.push_str(" --project \"$CLAUDE_PROJECT_DIR\"");
    }
    if target.client == "muse-code" {
        return super::muse::install(target, context, settings, &command, write);
    }
    perform(target, context, &argv, &command, write).unwrap_or_else(|e| e.payload(target))
}

fn perform(
    target: &Target,
    context: &Context,
    argv: &[String],
    command: &str,
    write: bool,
) -> Result<Value, Failure> {
    files::evidence(target)?;
    let cursor = target.client == "cursor";
    let other = targets::hooks(
        &target.client,
        context,
        if target.level == "user" {
            "project"
        } else {
            "user"
        },
    );
    let document = if cursor {
        Some(cursor_document(&target.path, command)?)
    } else {
        None
    };
    let mut displaced = None;
    if other.path != target.path {
        if cursor {
            cursor_document(&other.path, command)?;
        }
        if installed(&other).is_some() {
            if target.level == "project" {
                return Err(if cursor {
                    Failure::new(
                        format!(
                            "Cursor already carries neosian hooks in {}; both would fire",
                            other.path.display()
                        ),
                        "keep the user hooks or remove them by hand",
                    )
                } else {
                    Failure::new(
                        format!(
                            "{} already carries the neosian hooks at the user level ({}); both would fire",
                            target.label,
                            other.path.display()
                        ),
                        format!(
                            "keep them and name this project's scope with NEOSIAN_SCOPE in the client's environment, or remove ours from {}",
                            other.path.display()
                        ),
                    )
                });
            }
            displaced = Some(other.path.display().to_string());
            if write && !cursor {
                displace(&other)?;
            }
        }
    }
    let plugin = target.plugin.then(|| {
        let template = if target.client == "pi" {
            include_str!("../../../../neosian/assets/clients/pi-record.ts")
        } else {
            include_str!("../../../../neosian/assets/clients/opencode-record.js")
        };
        template.replace(
            "__NEOSIAN_RECORD_ARGV__",
            &pyfmt::dumps_spaced(&json!(argv), true, false),
        )
    });
    let created = !target.path.exists() && (write || cursor);
    let fragment = fragment(command, cursor);
    if write {
        if let Some(plugin) = &plugin {
            files::write_text(&target.path, plugin)?;
        } else {
            let document = match document {
                Some(document) => document,
                None => merge(files::load(&target.path)?, &fragment, &target.path)?,
            };
            files::write(&target.path, &document, cursor)?;
        }
        if cursor && displaced.is_some() {
            files::write(&other.path, &strip(files::load(&other.path)?), true)?;
        }
    }
    let mut payload = json!({"success": true, "client": target.client, "label": target.label, "level": target.level,
        "config_path": target.path, "hooks": if target.plugin { Value::Null } else { fragment["hooks"].clone() },
        "plugin": plugin, "command": command, "displaced": displaced, "written": write, "created": created});
    if !cursor {
        payload["trust_hint"] = json!(target.trust);
    }
    Ok(payload)
}

fn displace(target: &Target) -> Result<(), Failure> {
    if target.plugin {
        fs::remove_file(&target.path)?;
    } else {
        files::write(&target.path, &strip(files::load(&target.path)?), false)?;
    }
    Ok(())
}
