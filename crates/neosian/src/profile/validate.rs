//! Fail closed on unknown formats, ambiguous routing and unsupported selections.

use super::model::{CaptureMode, Injection, Profile};
use crate::conversation::ids::check_conversation_id;
use crate::store::grammar::{check_scope, validate_document_path};
use std::collections::HashSet;

pub fn name(value: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > 64
        || !value.as_bytes()[0].is_ascii_lowercase()
        || !value
            .bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || b"_-".contains(&c))
    {
        return Err("profile/source names must match [a-z][a-z0-9_-]{0,63}".into());
    }
    Ok(())
}

pub fn uuid(value: &str) -> Result<(), String> {
    if uuid::Uuid::parse_str(value)
        .ok()
        .is_none_or(|id| id.to_string() != value)
    {
        return Err("workspace/installation ID must be a canonical lowercase UUID".into());
    }
    Ok(())
}

fn unique(values: &[String], kind: &str) -> Result<(), String> {
    if values.iter().collect::<HashSet<_>>().len() != values.len() {
        return Err(format!("duplicate {kind}"));
    }
    Ok(())
}

fn bounded(values: &[String], kind: &str) -> Result<(), String> {
    unique(values, kind)?;
    if values.len() > 256 || values.iter().any(|v| v.is_empty() || v.len() > 4096) {
        return Err(format!(
            "{kind} requires at most 256 nonempty values of at most 4096 bytes"
        ));
    }
    Ok(())
}

pub fn profile(p: &Profile) -> Result<(), String> {
    if p.profile_format != 1 {
        return Err("unsupported profile_format; expected 1".into());
    }
    uuid(&p.workspace_id)?;
    if p.name.is_empty() || p.name.len() > 128 || p.name.chars().any(char::is_control) {
        return Err(
            "profile display name must be 1 to 128 bytes with no control characters".into(),
        );
    }
    if p.sources.is_empty() || p.sources.len() > 32 || p.mounts.len() > 32 || p.mounts.is_empty() {
        return Err("profiles require 1 to 32 sources and mounts".into());
    }
    unique(&p.sources, "source")?;
    for source in &p.sources {
        name(source)?;
    }
    let source = |s: &str| {
        if p.sources.iter().any(|known| known == s) {
            Ok(())
        } else {
            Err("undeclared profile source".to_string())
        }
    };
    let mut paths = HashSet::new();
    let mut scopes = std::collections::HashMap::new();
    for mount in &p.mounts {
        source(&mount.source)?;
        validate_document_path(&mount.path).map_err(|_| "invalid mount path")?;
        if mount.path.contains('/') || !paths.insert(&mount.path) {
            return Err("mount paths must be unique single segments".into());
        }
        check_scope(&mount.scope).map_err(|_| "invalid mount scope")?;
        if scopes.insert(&mount.scope, &mount.source).is_some() {
            return Err("each profile scope must have exactly one mount".into());
        }
        bounded(&mount.documents, "full-context documents")?;
        if (mount.injection == Injection::Full) == mount.documents.is_empty() {
            return Err("only full injection names a nonempty documents list".into());
        }
        for document in &mount.documents {
            validate_document_path(document).map_err(|_| "invalid full-context document path")?;
            if ["sessions", "messages", "message-links", "handoff"]
                .contains(&document.split('/').next().unwrap_or(""))
            {
                return Err(
                    "full injection cannot select session, handoff or mailbox records".into(),
                );
            }
        }
    }
    if !(512..=65_536).contains(&p.context.max_chars) {
        return Err("context.max_chars must be 512 to 65536".into());
    }
    bounded(&p.skills, "skills")?;
    for skill in &p.skills {
        if let Some(builtin) = skill.strip_prefix("builtin:") {
            if !["handoff", "upkeep"].contains(&builtin) {
                return Err("unknown built-in skill".into());
            }
        } else {
            let segments: Vec<_> = skill.strip_prefix('/').unwrap_or("").split('/').collect();
            if segments.len() != 3
                || !paths.iter().any(|path| path.as_str() == segments[0])
                || segments[1] != "skills"
            {
                return Err("skills must name /mount/skills/name or builtin:name".into());
            }
            validate_document_path(segments[2]).map_err(|_| "invalid skill name")?;
        }
    }
    if p.skill_pins.len() > 256 {
        return Err("too many skill pins".into());
    }
    for (path, pin) in &p.skill_pins {
        if !p.skills.contains(path) {
            return Err("skill pin must name a selected stored skill".into());
        }
        let (mount, _) =
            crate::skill::model::address(path).map_err(|_| "pin must name /mount/skills/name")?;
        if !p
            .mounts
            .iter()
            .any(|m| m.path == mount && m.scope == pin.scope && m.source == pin.source)
        {
            return Err("skill pin source/scope must match its selected mount".into());
        }
        crate::skill::model::Revision {
            version: pin.version,
            sha256: pin.sha256.clone(),
        }
        .validate()
        .map_err(|_| "invalid pinned revision")?;
    }
    let c = &p.capture;
    if c.fields.len() > 5
        || c.fields
            .iter()
            .enumerate()
            .any(|(i, f)| c.fields[..i].contains(f))
    {
        return Err("capture.fields must be unique".into());
    }
    bounded(&c.exclude_tools, "excluded tool names")?;
    bounded(&c.remove_keys, "removed structured keys")?;
    if c.replacements.len() > 256
        || c.replacements
            .iter()
            .any(|r| r.from.is_empty() || r.from.len() > 4096 || r.to.len() > 4096)
    {
        return Err("capture replacements require nonempty patterns and bounded values".into());
    }
    if let Some(history) = &p.history {
        source(&history.source)?;
        check_scope(&history.scope).map_err(|_| "invalid history scope")?;
        if scopes
            .get(&history.scope)
            .is_some_and(|source| *source != &history.source)
        {
            return Err("a scope cannot route to two sources in one profile".into());
        }
        if history.prefix.is_some() == !history.conversations.is_empty() {
            return Err("history requires either prefix or explicit conversations".into());
        }
        if let Some(prefix) = &history.prefix {
            check_conversation_id(prefix).map_err(|_| "invalid history namespace")?;
            if prefix.len() > 64 {
                return Err("history prefix exceeds 64 bytes".into());
            }
        }
        bounded(&history.conversations, "history conversations")?;
        for id in &history.conversations {
            check_conversation_id(id).map_err(|_| "invalid history conversation ID")?;
        }
        if c.mode != CaptureMode::Off
            && history.prefix.as_deref() != Some(p.conversation_prefix().as_str())
        {
            return Err(
                "capture requires the workspace's w-<UUID without hyphens>- history prefix".into(),
            );
        }
        if c.mode != CaptureMode::Off
            && p.mounts
                .iter()
                .any(|m| m.scope == history.scope && m.access != super::model::Access::Rw)
        {
            return Err("capture history cannot use a read-only or edit-only mount".into());
        }
    } else if c.mode != CaptureMode::Off {
        return Err("capture requires an explicit history source".into());
    }
    Ok(())
}
