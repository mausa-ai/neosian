//! The hooks part of a refresh. Groups that already carry our command are kept
//! byte for byte with everything a person added to them (§40.48, #426).

use super::{
    context::Context,
    hooks,
    refresh_parts::{Change, Part, RECORD, RETRY, load, recognized},
    targets,
};
use serde_json::Value;
use std::collections::BTreeSet;

/// What the hooks need to match this release, or `None` when they already do:
/// our command once in each event the release registers and in no other. An
/// existing group is never rewritten. A missing event gains a fresh group; an
/// event the release no longer registers loses our hook alone, then the group
/// and the event if nothing else is left in them.
fn reconcile(document: &Value, fragment: &Value, cursor: bool) -> Result<Option<Value>, String> {
    if cursor && document["version"] != 1 {
        return Err(format!(
            "Cursor hooks need version 1; fix the file, {RETRY}"
        ));
    }
    let wanted = fragment["hooks"].as_object().expect("fragment");
    let mut updated = document.clone();
    let Some(present) = updated["hooks"].as_object_mut() else {
        return Err(format!(
            "hooks is not a JSON object; fix it by hand, {RETRY}"
        ));
    };
    let mut changed = false;
    for (event, groups) in wanted {
        match present.get_mut(event) {
            None => {
                present.insert(event.clone(), groups.clone());
                changed = true;
            }
            Some(Value::Array(existing)) => match hooks::count(existing) {
                0 => {
                    existing.extend(groups.as_array().expect("fragment groups").clone());
                    changed = true;
                }
                1 => {}
                n => {
                    return Err(format!(
                        "our hook appears {n} times in {event}; remove the extra ones, {RETRY}"
                    ));
                }
            },
            Some(_) => {
                return Err(format!(
                    "hooks.{event} is not a JSON array; fix it by hand, {RETRY}"
                ));
            }
        }
    }
    let stale: Vec<String> = present
        .iter()
        .filter(|(event, groups)| {
            !wanted.contains_key(*event) && groups.as_array().is_some_and(|g| hooks::count(g) > 0)
        })
        .map(|(event, _)| event.clone())
        .collect();
    for event in stale {
        let groups = present[&event].as_array_mut().expect("an array");
        hooks::remove(groups);
        if groups.is_empty() {
            present.shift_remove(&event);
        }
        changed = true;
    }
    Ok(changed.then_some(updated))
}

/// The hooks part; `pending` is the client's planned MCP change, which Muse keeps
/// in the same `settings.json` as its hooks, so both land in one document.
pub fn hooks(client: &str, context: &Context, pending: Option<&Part>) -> Option<Part> {
    let carrying: Vec<_> = targets::active(client, context, "user")
        .into_iter()
        .filter_map(|target| hooks::installed(&target).map(|argv| (target, argv)))
        .collect();
    let (target, argv) = carrying.first()?;
    let mut part = Part::new("hooks", target.path.clone());
    if carrying.len() > 1 {
        return Some(part.left_alone(format!(
            "the hooks are present in more than one file; remove the copy you do not use, {RETRY}"
        )));
    }
    if let Err(reason) = recognized(argv, "record", &RECORD, context) {
        return Some(part.left_alone(reason));
    }
    part.executable = Some(argv[0].clone());
    if target.plugin {
        let text = hooks::adapter(client, argv);
        return Some(match std::fs::read_to_string(&target.path) {
            Ok(existing) if existing == text => part,
            Ok(existing) => part
                .update(Change::Text(text), Some(existing))
                .guarded(std::slice::from_ref(&target.path)),
            Err(error) => part.refused(&error.into()),
        });
    }
    let loaded = match pending {
        Some(Part {
            path,
            change: Some(Change::Json(document, _)),
            read,
            ..
        }) if *path == target.path => Ok((read.clone(), document.clone())),
        _ => {
            let settings =
                client == "muse-code" && target.path == target.evidence.join("settings.json");
            load(&target.path, settings)
        }
    };
    let (read, document) = match loaded {
        Ok(loaded) => loaded,
        Err(failure) => return Some(part.refused(&failure)),
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
        return Some(part.left_alone("the hook commands differ between events"));
    };
    let cursor = client == "cursor";
    match reconcile(&document, &hooks::fragment(command, cursor), cursor) {
        Err(reason) => Some(part.left_alone(reason)),
        Ok(None) => Some(part),
        Ok(Some(updated)) => {
            part.note.clone_from(&target.trust);
            Some(
                part.update(Change::Json(updated, cursor), read)
                    .guarded(std::slice::from_ref(&target.path)),
            )
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::Scratch;
    use serde_json::json;
    use std::collections::BTreeMap;

    #[test]
    fn hooks_in_the_file_of_a_planned_mcp_change_build_on_its_document() {
        let scratch = Scratch::new("refresh-one-document");
        scratch.plant("neosian", "");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let path = scratch.path().join("neosian");
            std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700)).unwrap();
        }
        let executable = scratch.path().join("neosian").display().to_string();
        let command = format!("{executable} record --agent muse-code");
        let group = json!([{"hooks": [{"type": "command", "command": command}]}]);
        let text = json!({"schema_version": 1, "hooks": {"Stop": group}}).to_string();
        scratch.plant("muse/settings.json", &text);
        let context = Context {
            home: scratch.path().into(),
            cwd: scratch.path().into(),
            executable,
            env: BTreeMap::from([(
                "XDG_CONFIG_HOME".into(),
                scratch.path().display().to_string(),
            )]),
            directories: BTreeMap::new(),
        };
        let path = scratch.path().join("muse/settings.json");
        let mut planned: Value = serde_json::from_str(&text).unwrap();
        planned["mcpServers"] = json!({"neosian-memory": {"planned": true}});
        let pending =
            Part::new("mcp", path.clone()).update(Change::Json(planned, false), Some(text.clone()));
        let part = hooks("muse-code", &context, Some(&pending)).unwrap();
        let Some(Change::Json(document, false)) = &part.change else {
            panic!("the hooks part plans one document: {:?}", part.reason);
        };
        assert_eq!(part.read.as_deref(), Some(text.as_str()));
        assert_eq!(document["mcpServers"]["neosian-memory"]["planned"], true);
        assert_eq!(document["hooks"]["SessionStart"], group);
        assert_eq!(document["hooks"]["Stop"], group);
    }
}
