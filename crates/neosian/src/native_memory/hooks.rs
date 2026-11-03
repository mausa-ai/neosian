//! Scan registered sources at supported hook boundaries, independent of tool choice.

use super::{config, error_text, invalid, local, sync};
use crate::memory::mounts::Mount;
use crate::record::payload::Payload;
use crate::remote::selection::Selection;
use crate::store::error::Error;
use crate::store::grammar::Actor;
use serde_json::Value;
use std::io::Write;
use std::path::Path;

fn capture(
    selection: &Selection,
    mounts: &[Mount],
    agent: &str,
    project: &Path,
    payload: &Payload,
    err: &mut impl Write,
) -> Result<Option<String>, Error> {
    let event = payload
        .get("hook_event_name")
        .and_then(Value::as_str)
        .unwrap_or("");
    if !matches!(agent, "claude-code" | "codex")
        || !matches!(
            event,
            "SessionStart" | "UserPromptSubmit" | "PostToolUse" | "Stop"
        )
        || payload
            .get("agent_id")
            .is_some_and(|v| !crate::conversation::message::falsy(v))
        || !config::path()?.exists()
    {
        return Ok(None);
    }
    let _held = crate::store::lock::acquire(&config::path()?.with_extension("lock"))?;
    let registry = config::read()?;
    let project = config::absolute(project)?;
    let sources: Vec<_> = registry
        .sources
        .iter()
        .filter(|s| s.matches(agent, &project))
        .collect();
    if sources.is_empty() {
        return Ok(None);
    }
    let root = local(selection)?;
    let session = payload
        .get("session_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    crate::conversation::ids::parse_conversation_id(session)
        .map_err(|_| invalid("invalid native memory session"))?;
    let actor = Actor::parse(&format!("{agent}:{session}")).map_err(invalid)?;
    let mut changed = 0;
    let mut failed = 0;
    let mut destinations = Vec::new();
    for source in sources {
        // An alternate store is not the destination this registration authorized.
        if source.store != root {
            continue;
        }
        destinations.push(source.destination.as_str());
        match sync(source, &root, mounts, &actor, true) {
            Ok(report) => {
                for action in report["actions"].as_array().expect("actions") {
                    match action["action"].as_str() {
                        Some("create" | "replace") => changed += 1,
                        Some("refused" | "recovery_required" | "not_attempted") => {
                            failed += 1;
                            writeln!(
                                err,
                                "native memory {}: {}",
                                source.name,
                                action["error"].as_str().unwrap_or("capture refused")
                            )?;
                        }
                        _ => {}
                    }
                }
            }
            Err(error) => {
                failed += 1;
                writeln!(err, "native memory {}: {}", source.name, error_text(error))?;
            }
        }
    }
    // Stop does not accept informational context; the next prompt gives the reminder.
    // SessionStart already includes captured paths in its bounded index.
    if destinations.is_empty()
        || !matches!(event, "UserPromptSubmit" | "PostToolUse")
        || event == "PostToolUse" && changed == 0 && failed == 0
    {
        return Ok(None);
    }
    let template = crate::assets::literal(crate::assets::MEMORY, "native_capture")
        .expect("native capture asset");
    let paths = destinations.join(", ");
    Ok(Some(crate::assets::render(
        &template,
        &[
            ("changed", &changed.to_string()),
            ("failed", &failed.to_string()),
            ("destinations", &paths),
        ],
    )))
}

pub(crate) fn run(
    selection: &Selection,
    mounts: &[Mount],
    agent: &str,
    project: &Path,
    payload: &Payload,
    err: &mut impl Write,
) -> Option<String> {
    match capture(selection, mounts, agent, project, payload, err) {
        Ok(context) => context,
        Err(error) => {
            let _ = writeln!(
                err,
                "native memory capture unavailable: {}",
                error_text(error)
            );
            None
        }
    }
}
