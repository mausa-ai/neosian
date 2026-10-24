//! Skills as documents in a mount (`memory/skills.py`, `shared/skill.py`,
//! `shared/frontmatter.py`, DESIGN §24): a skill is the document
//! `skills/<name>` under any mount, markdown with a frontmatter whose
//! `description` says when to use it; the binary's shipped skills are a
//! directory source listed after the mounts'. Read live per call.

use super::mounts::{Mount, resolve};
use crate::assets::SKILLS;
use crate::conversation::Error;
use crate::state::State;

pub const SKILLS_PREFIX: &str = "skills/";
const FENCE: &str = "---";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Skill {
    pub name: String,
    pub description: String,
    pub content: String,
}

/// One skill by address: parsed, or the error its author must fix.
/// `path` and `version` are the store's; a directory skill has neither.
#[derive(Debug)]
pub struct SkillEntry {
    pub name: String,
    pub path: Option<String>,
    pub skill: Option<Skill>,
    pub version: Option<u64>,
    pub error: Option<String>,
    pub details: Option<serde_json::Value>,
}

fn invalid(where_: &str, reason: Option<&str>) -> String {
    let message = format!("Invalid or missing frontmatter in skill file: {where_}");
    match reason {
        Some(reason) => format!("{message} — {reason}"),
        None => message,
    }
}

/// A double-quoted scalar with JSON's escapes, a comment allowed after
/// it and nothing else.
fn quoted(value: &str) -> Option<String> {
    let mut escaped = false;
    let close = value[1..].char_indices().find_map(|(at, ch)| match ch {
        '\\' if !escaped => {
            escaped = true;
            None
        }
        '"' if !escaped => Some(at + 1),
        _ => {
            escaped = false;
            None
        }
    })?;
    let rest = value[close + 1..].trim();
    if !rest.is_empty() && !rest.starts_with('#') {
        return None;
    }
    serde_json::from_str::<String>(&value[..=close]).ok()
}

/// A plain scalar as YAML 1.1 types it: a string, or something else.
fn is_string(value: &str) -> bool {
    const NOT: [&str; 22] = [
        "", "~", "null", "Null", "NULL", "true", "True", "TRUE", "false", "False", "FALSE", "yes",
        "Yes", "YES", "no", "No", "NO", "on", "On", "ON", "off", "Off",
    ];
    if NOT.contains(&value) || value == "OFF" {
        return false;
    }
    let digits = value.trim_start_matches(['-', '+']);
    let number = !digits.is_empty()
        && digits
            .bytes()
            .all(|b| b.is_ascii_digit() || b == b'.' || b == b'_')
        && digits.bytes().any(|b| b.is_ascii_digit());
    !number
}

/// The keys of a frontmatter, each with its value when that is a string.
type Fields = Vec<(String, Option<String>)>;

/// The frontmatter mapping, bounded to one `key: value` per line with a
/// one-line scalar (`parse_frontmatter`'s YAML as a skill plausibly
/// writes it): the keys and whether each value is a string, and the body.
fn frontmatter(content: &str, where_: &str) -> Result<(Fields, String), String> {
    let text = content.trim();
    let lines: Vec<&str> = text.split('\n').collect();
    if lines[0].trim_end_matches('\r') != FENCE {
        return Err(invalid(where_, None));
    }
    let Some(close) = (1..lines.len()).find(|&index| lines[index].trim_end_matches('\r') == FENCE)
    else {
        return Err(invalid(where_, None));
    };
    let mut fields: Fields = Vec::new();
    for line in &lines[1..close] {
        let line = line.trim_end_matches('\r');
        if line.trim().is_empty() || line.trim_start().starts_with('#') {
            continue;
        }
        let Some((key, value)) = line.split_once(':') else {
            return Err(invalid(where_, None));
        };
        if line.starts_with([' ', '\t', '-']) || key.trim().is_empty() {
            return Err(invalid(where_, None));
        }
        if !value.is_empty() && !value.starts_with(' ') {
            return Err(invalid(where_, None));
        }
        let value = value.trim();
        let value = match value.find(" #") {
            Some(at) if !value.starts_with(['"', '\'']) => value[..at].trim(),
            _ => value,
        };
        let typed = if value.starts_with('"') {
            match quoted(value) {
                Some(text) => Some(text),
                None => return Err(invalid(where_, None)),
            }
        } else if let Some(inner) = value.strip_prefix('\'') {
            match inner.strip_suffix('\'') {
                Some(inner) => Some(inner.replace("''", "'")),
                None => return Err(invalid(where_, None)),
            }
        } else if value.contains(": ")
            || value.starts_with(['[', '{', '|', '>', '&', '*', '!', '%', '@', '`'])
        {
            return Err(invalid(where_, None));
        } else {
            is_string(value).then(|| value.to_string())
        };
        fields.retain(|(known, _)| known != key.trim());
        fields.push((key.trim().to_string(), typed));
    }
    let body = lines[close + 1..].join("\n").trim().to_string();
    Ok((fields, body))
}

/// A skill document whose address is `name`; `where_` names the source
/// in errors. The sentences are the Python side's.
pub fn skill_from_document(name: &str, content: &str, where_: &str) -> Result<Skill, String> {
    let owned;
    let content = if content.starts_with("<!-- neosian-knowledge:") {
        owned = crate::skill::model::parse(content)
            .map_err(|e| crate::conversation::Error::from(e).text())?
            .0
            .body;
        owned.as_str()
    } else {
        content
    };
    let (fields, body) = frontmatter(content, where_)?;
    let field = |key: &str| fields.iter().find(|(known, _)| known == key);
    let Some((_, description)) = field("description") else {
        return Err(format!(
            "Missing required key 'description' in skill frontmatter: {where_}"
        ));
    };
    let Some(description) = description else {
        return Err(invalid(where_, Some("description must be a string")));
    };
    if let Some((_, declared)) = field("name") {
        let declared = declared.as_deref().unwrap_or("None");
        if declared != name {
            let reason = format!(
                "name {} does not match the address {}",
                crate::repr::repr(declared),
                crate::repr::repr(name)
            );
            return Err(invalid(where_, Some(&reason)));
        }
    }
    Ok(Skill {
        name: name.to_string(),
        description: description.clone(),
        content: body,
    })
}

/// The binary's shipped skills, in name order.
pub fn shipped_skills() -> Vec<Skill> {
    SKILLS
        .iter()
        .map(|(name, text)| {
            skill_from_document(name, text, &format!("assets/skills/{name}.md"))
                .expect("a shipped skill parses")
        })
        .collect()
}

fn read(
    store: &dyn State,
    mounts: &[Mount],
    mount: &Mount,
    name: &str,
) -> Result<Option<SkillEntry>, Error> {
    if name.contains('/') {
        return Ok(None);
    }
    let Some(document) = store.read(&mount.scope, &format!("{SKILLS_PREFIX}{name}"))? else {
        return Ok(None);
    };
    if document.envelope.redacted {
        return Ok(None);
    }
    let path = format!("/{}/{SKILLS_PREFIX}{name}", mount.path);
    let version = Some(document.envelope.version);
    let checked = crate::skill::runtime::check(
        store,
        mounts,
        &path,
        &document.content,
        document.envelope.version,
        true,
        &Default::default(),
    )
    .map_err(|e| crate::conversation::Error::from(e).text());
    let show_details =
        store.profile().is_some() || document.content.starts_with("<!-- neosian-knowledge:");
    let parsed = skill_from_document(name, &document.content, &path)
        .and_then(|skill| checked.map(|details| (skill, details)));
    Ok(Some(match parsed {
        Ok((skill, details)) => SkillEntry {
            name: name.to_string(),
            path: Some(path),
            skill: Some(skill),
            version,
            error: None,
            details: show_details.then_some(details),
        },
        Err(error) => SkillEntry {
            name: name.to_string(),
            path: Some(path),
            skill: None,
            version,
            error: Some(error),
            details: None,
        },
    }))
}

fn shipped_entry(skill: &Skill) -> SkillEntry {
    SkillEntry {
        name: skill.name.clone(),
        path: None,
        skill: Some(skill.clone()),
        version: None,
        error: None,
        details: None,
    }
}

/// Every skill: the mounts in order, then the directory.
pub fn list_skills(
    store: &dyn State,
    mounts: &[Mount],
    directory: &[Skill],
) -> Result<Vec<SkillEntry>, Error> {
    if let Some(profile) = store.profile() {
        let mut entries = Vec::new();
        for selected in &profile.skills {
            let entry = if let Some(builtin) = selected.strip_prefix("builtin:") {
                directory
                    .iter()
                    .find(|s| s.name == builtin)
                    .map(shipped_entry)
            } else {
                let (mount, path) = resolve(mounts, selected)?;
                read(
                    store,
                    mounts,
                    mount,
                    path.strip_prefix(SKILLS_PREFIX).unwrap_or(""),
                )?
            };
            let mut entry = entry.unwrap_or_else(|| SkillEntry {
                name: selected.clone(),
                path: Some(selected.clone()),
                skill: None,
                version: None,
                error: Some("Selected skill is unavailable or redacted".into()),
                details: None,
            });
            if selected.starts_with("builtin:") && entry.skill.is_some() {
                entry.details = Some(
                    serde_json::json!({"source":"builtin","path":selected,"binary_version":env!("CARGO_PKG_VERSION")}),
                );
            }
            entry.name = selected.clone();
            if let Some(skill) = &mut entry.skill {
                skill.name = selected.clone();
            }
            entries.push(entry);
        }
        return Ok(entries);
    }
    let mut entries = Vec::new();
    for mount in mounts {
        let listings = store.list_documents(&mount.scope, SKILLS_PREFIX)?;
        if listings.len() > 10_000 {
            return Err(crate::store::error::Error::Value(
                "skill discovery exceeds 10000 entries".into(),
            )
            .into());
        }
        for listing in listings {
            let bare = listing.path.strip_prefix(SKILLS_PREFIX).ok_or_else(|| {
                crate::store::error::Error::Value(
                    "backend returned an out-of-prefix skill path".into(),
                )
            })?;
            if let Some(entry) = read(store, mounts, mount, bare)? {
                entries.push(entry);
            }
        }
    }
    entries.extend(directory.iter().map(shipped_entry));
    Ok(entries)
}

/// One skill by name (the first in listing order) or by its
/// `/mount/skills/name` path; None when nothing is there.
pub fn load_skill(
    store: &dyn State,
    mounts: &[Mount],
    directory: &[Skill],
    name: &str,
) -> Result<Option<SkillEntry>, Error> {
    if store.profile().is_some() {
        return Ok(list_skills(store, mounts, directory)?
            .into_iter()
            .find(|e| e.name == name));
    }
    if let Some(builtin) = name.strip_prefix("builtin:") {
        return Ok(directory
            .iter()
            .find(|skill| skill.name == builtin)
            .map(shipped_entry));
    }
    if name.starts_with('/') {
        let (mount, rest) = resolve(mounts, name)?;
        let Some(bare) = rest.strip_prefix(SKILLS_PREFIX) else {
            return Ok(None);
        };
        return read(store, mounts, mount, bare);
    }
    for mount in mounts {
        if let Some(entry) = read(store, mounts, mount, name)? {
            return Ok(Some(entry));
        }
    }
    Ok(directory
        .iter()
        .find(|skill| skill.name == name)
        .map(shipped_entry))
}

#[cfg(test)]
mod tests;
