//! Muse's validated ordinary/managed hook transition (`record/muse.py`).

use super::{
    context::Context,
    files::{self, FIX, Failure},
    hooks,
    settings::Settings,
    targets::{self, Target},
};
use serde_json::{Value, json};
use std::path::PathBuf;

type Changes = Vec<(PathBuf, Value)>;

pub fn install(
    target: &Target,
    context: &Context,
    settings: &Settings,
    command: &str,
    write: bool,
) -> Value {
    let mut target = target.clone();
    target.path = target.evidence.join("settings.json");
    match documents(&target, context, settings, command) {
        Err(e) => e.payload(&target),
        Ok((path, changes)) => {
            target.path = path;
            let created = !target.path.exists();
            if write {
                for (path, document) in changes
                    .iter()
                    .filter(|(p, _)| *p == target.path)
                    .chain(changes.iter().filter(|(p, _)| *p != target.path))
                {
                    if let Err(e) = files::write(path, document, false) {
                        return e.payload(&target);
                    }
                }
            }
            let mut preview = json!({});
            for (path, _) in &changes {
                let mut change = json!({"remove_neosian_hooks": *path != target.path,
                    "merge": if *path == target.path { hooks::fragment(command, false) } else { json!({}) }});
                if *path == target.evidence.join("settings.json") {
                    change["merge"]["schema_version"] = json!(1);
                    if !settings.credentials.is_empty() {
                        change["merge"]["managed_hooks_path"] =
                            json!(target.path.file_name().unwrap().to_string_lossy());
                        change["add_managed_hooks_env_vars"] = json!(settings.credentials);
                    }
                }
                preview[path.display().to_string()] = change;
            }
            json!({"success": true, "client": "muse-code", "label": "Muse Code", "level": target.level,
                "config_path": target.path, "command": command, "hooks": hooks::fragment(command, false)["hooks"],
                "plugin": null, "written": write, "created": created, "displaced": null, "files": preview})
        }
    }
}

fn documents(
    target: &Target,
    context: &Context,
    settings: &Settings,
    command: &str,
) -> Result<(PathBuf, Changes), Failure> {
    files::evidence(target)?;
    let user_path = target.evidence.join("settings.json");
    let mut user = files::settings(&user_path)?;
    let credentialed = !settings.credentials.is_empty();
    if credentialed && target.level == "project" {
        return Err(Failure::new(
            "Muse credential-backed hooks require user level",
            "re-run with --level user: managed hooks are machine-wide",
        ));
    }
    let owned = target.evidence.join("neosian-hooks.json");
    let managed = targets::managed(&user, &user_path);
    if credentialed
        && let Some(managed) = &managed
        && *managed != owned
    {
        return Err(Failure::new(
            format!(
                "Muse already uses a managed hooks file: {}",
                managed.display()
            ),
            "merge the neosian hooks and required environment variable names into your managed configuration by hand; its pointer is preserved",
        ));
    }
    if user.get("managed_hooks_path").is_some() && managed.is_none() {
        return Err(Failure::new(
            "Muse managed_hooks_path must be a nonempty path",
            FIX,
        ));
    }
    let path = if target.level == "project" {
        context.cwd.join(".muse/hooks.json")
    } else if credentialed {
        owned.clone()
    } else {
        user_path.clone()
    };
    let fragment = hooks::fragment(command, false);
    let mut changes: Changes = vec![];
    for level in ["user", "project"] {
        for source in targets::active("muse-code", context, level) {
            let document = if source.path == user_path {
                user.clone()
            } else {
                files::load(&source.path)?
            };
            hooks::merge(document.clone(), &fragment, &source.path)?;
            if source.path == path || hooks::installed(&source).is_none() {
                continue;
            }
            if target.level == "project"
                || (managed.as_ref() == Some(&source.path) && source.path != owned)
            {
                return Err(Failure::new(
                    format!(
                        "Muse already carries neosian hooks in {}; both would fire",
                        source.path.display()
                    ),
                    "keep the user-level hooks, or remove those handlers by hand",
                ));
            }
            changes.push((source.path, hooks::strip(document)));
        }
    }
    let document = if path == user_path {
        user.clone()
    } else {
        files::load(&path)?
    };
    changes.push((path.clone(), hooks::merge(document, &fragment, &path)?));
    if credentialed {
        if let Some((_, changed)) = changes.iter().find(|(p, _)| *p == user_path) {
            user = changed.clone();
        }
        let forwarded = user
            .get("managed_hooks_env_vars")
            .cloned()
            .unwrap_or(json!([]));
        let Some(forwarded) = forwarded
            .as_array()
            .filter(|v| v.iter().all(Value::is_string))
        else {
            return Err(Failure::new(
                "Muse managed_hooks_env_vars must be a string array",
                FIX,
            ));
        };
        let mut names = forwarded.clone();
        for name in &settings.credentials {
            if !names.contains(&json!(name)) {
                names.push(json!(name));
            }
        }
        user["managed_hooks_path"] = json!("neosian-hooks.json");
        user["managed_hooks_env_vars"] = json!(names);
        if let Some((_, document)) = changes.iter_mut().find(|(p, _)| *p == user_path) {
            *document = user;
        } else {
            changes.push((user_path, user));
        }
    }
    Ok((path, changes))
}
