//! Preflight whole batches; receipts make partial failures explicit.

use serde_json::{Value, json};

use super::input::Input;
use super::model::{DEFINITION, MAX_DOCUMENTS, invalid, ordinary};
use super::rules::{Definition, Issue};
use super::{definition, snapshot, target, text_error};
use crate::memory::dispatch::Memory;
use crate::memory::mounts::{Mount, structural, writable};
use crate::store::{Document, error::Error};

struct Change {
    path: String,
    content: String,
    before: Option<Value>,
    action: &'static str,
}

fn observed(memory: &Memory, mount: &Mount, path: &str) -> Result<Option<Document>, Error> {
    let found = memory.store.read(&mount.scope, path)?;
    if found.as_ref().is_some_and(|d| d.envelope.redacted) {
        return Err(invalid(format!("refusing redacted destination: {path}")));
    }
    if found.is_none() && !memory.store.versions(&mount.scope, path, 1)?.is_empty() {
        return Err(invalid(format!(
            "refusing absent destination with retained history: {path}"
        )));
    }
    Ok(found)
}

fn change(
    memory: &Memory,
    mount: &Mount,
    path: String,
    content: String,
    conflict: &str,
) -> Result<Change, Error> {
    let found = observed(memory, mount, &path)?;
    let action = match &found {
        None => {
            structural(mount)?;
            "create"
        }
        Some(doc) if doc.content == content => "duplicate",
        Some(_) if conflict == "skip" => "skip",
        Some(_) if conflict == "replace" => "replace",
        Some(_) => "conflict",
    };
    Ok(Change {
        before: snapshot(&mount.scope, &path, found.as_ref())?,
        path,
        content,
        action,
    })
}

fn collisions(a: &str, b: &str) -> bool {
    for (left, right) in a.split('/').zip(b.split('/')) {
        if !left.eq_ignore_ascii_case(right) {
            break;
        }
        if left != right {
            return true;
        }
    }
    false
}

fn existing_issues(
    memory: &Memory,
    mount: &Mount,
    prefix: &str,
    rules: &Definition,
    planned: &[Change],
) -> Result<Vec<Issue>, Error> {
    let entries = memory
        .store
        .list_documents(&mount.scope, &format!("{prefix}/"))?;
    if entries.len() > MAX_DOCUMENTS + 1 {
        return Err(invalid("collection validation exceeds 1000 documents"));
    }
    let mut issues = vec![];
    let mut total = 0;
    for entry in entries {
        if !entry.path.starts_with(&format!("{prefix}/")) {
            return Err(invalid(
                "backend returned a document outside the selected collection",
            ));
        }
        if entry.path == format!("{prefix}/{DEFINITION}") || entry.redacted {
            continue;
        }
        if entry.path.ends_with(&format!("/{DEFINITION}")) {
            return Err(invalid(
                "nested collections require separate definition changes",
            ));
        }
        if planned
            .iter()
            .any(|p| p.path == entry.path && matches!(p.action, "create" | "replace"))
        {
            continue;
        }
        if let Some(doc) = memory.store.read(&mount.scope, &entry.path)? {
            if doc.envelope.redacted {
                continue;
            }
            total += doc.content.len();
            if total > super::model::MAX_TOTAL {
                return Err(invalid("collection validation exceeds 20 MiB"));
            }
            let content = super::model::Content::parse(&doc.content, prefix)?;
            if content.metadata.collection != prefix {
                issues.push(Issue {
                    path: entry.path.clone(),
                    field: "collection".into(),
                    message: "document names a different collection".into(),
                });
            }
            issues.extend(rules.check(&entry.path, &content.metadata.attributes));
        }
    }
    Ok(issues)
}

fn apply(
    memory: &Memory,
    mount: &Mount,
    changes: Vec<Change>,
    write: bool,
    mut report: Value,
) -> Result<(Value, u8), Error> {
    let entries = memory.store.list_documents(&mount.scope, "")?;
    if entries.len() > 10_000 {
        return Err(invalid(
            "knowledge preflight exceeds 10000 current documents in this scope",
        ));
    }
    for change in &changes {
        if entries.iter().any(|e| collisions(&change.path, &e.path))
            || changes
                .iter()
                .any(|other| collisions(&change.path, &other.path))
        {
            return Err(invalid(format!(
                "case-colliding destination: {}",
                change.path
            )));
        }
    }
    report["preview"] = (!write).into();
    report["actions"] = json!(
        changes
            .iter()
            .map(|c| json!({"path": format!("/{}/{}", mount.path, c.path), "action": c.action}))
            .collect::<Vec<_>>()
    );
    report["applied"] = json!([]);
    let refused = changes.iter().any(|c| c.action == "conflict")
        || report["issues"].as_array().is_some_and(|a| !a.is_empty());
    report["success"] = (!refused).into();
    if refused || !write {
        return Ok((report, u8::from(refused)));
    }
    // An observed erase can keep its version. Compare the full current snapshot as well.
    // Apply still requires paused writers: wire 6 has no compare-and-create or batch CAS.
    let run = (|| -> Result<(), Error> {
        for change in changes
            .iter()
            .filter(|c| matches!(c.action, "create" | "replace"))
        {
            let current = observed(memory, mount, &change.path)?;
            if snapshot(&mount.scope, &change.path, current.as_ref())? != change.before {
                return Err(invalid(format!(
                    "destination changed during preflight: {}",
                    change.path
                )));
            }
        }
        for change in changes
            .iter()
            .filter(|c| matches!(c.action, "create" | "replace"))
        {
            let current = observed(memory, mount, &change.path)?;
            if snapshot(&mount.scope, &change.path, current.as_ref())? != change.before {
                return Err(invalid(format!(
                    "destination changed before write: {}",
                    change.path
                )));
            }
            let version = memory.store.write(
                &mount.scope,
                &change.path,
                &change.content,
                &memory.actor,
                current.map(|d| d.envelope.version),
            )?;
            report["applied"]
                .as_array_mut()
                .expect("receipts array")
                .push(
                    json!({"path": format!("/{}/{}", mount.path, change.path), "version": version}),
                );
        }
        Ok(())
    })();
    if let Err(error) = run {
        report["success"] = false.into();
        report["error"] = text_error(error).into();
        return Ok((report, 1));
    }
    Ok((report, 0))
}

pub fn import(
    memory: &Memory,
    path: &str,
    input: Input,
    conflict: &str,
    write: bool,
) -> Result<(Value, u8), Error> {
    let (mount, prefix) = target(memory, path)?;
    writable(mount)?;
    let existing = definition(memory, mount, prefix)?;
    let mut effective = existing.clone();
    let mut changes = vec![];
    if let Some(incoming) = input.collection {
        if existing.as_ref().is_none_or(|d| d == &incoming) || conflict != "skip" {
            effective = Some(incoming.clone());
        }
        changes.push(change(
            memory,
            mount,
            format!("{prefix}/{DEFINITION}"),
            incoming.render(),
            conflict,
        )?);
    }
    let mut issues = vec![];
    for item in input.items {
        let target = format!("{prefix}/{}", item.path);
        ordinary(&target)?;
        let planned = change(
            memory,
            mount,
            target.clone(),
            item.content.render()?,
            conflict,
        )?;
        if planned.action != "skip"
            && let Some(rules) = &effective
        {
            issues.extend(rules.check(&target, &item.content.metadata.attributes));
        }
        // A more specific collection must be imported separately under its own rules.
        let relative_parent = item.path.rsplit_once('/').map(|(p, _)| p);
        if let Some(parent) = relative_parent {
            let mut nested = prefix.to_string();
            for part in parent.split('/') {
                nested.push('/');
                nested.push_str(part);
                if memory
                    .store
                    .read(&mount.scope, &format!("{nested}/{DEFINITION}"))?
                    .is_some()
                {
                    return Err(invalid(format!(
                        "nested collection requires a separate import: {nested}"
                    )));
                }
            }
        }
        changes.push(planned);
    }
    if changes.iter().any(|c| {
        c.path == format!("{prefix}/{DEFINITION}") && matches!(c.action, "create" | "replace")
    }) && let Some(rules) = &effective
    {
        issues.extend(existing_issues(memory, mount, prefix, rules, &changes)?);
    }
    apply(
        memory,
        mount,
        changes,
        write,
        json!({"operation":"import", "destination":path, "issues":issues, "ignored":input.ignored}),
    )
}

pub fn collection(
    memory: &Memory,
    path: &str,
    incoming: Option<Definition>,
    write: bool,
) -> Result<(Value, u8), Error> {
    let (mount, prefix) = target(memory, path)?;
    let existing = definition(memory, mount, prefix)?;
    let Some(incoming) = incoming else {
        return Ok((json!({"path":path,"definition":existing}), 0));
    };
    writable(mount)?;
    let change = change(
        memory,
        mount,
        format!("{prefix}/{DEFINITION}"),
        incoming.render(),
        "replace",
    )?;
    let issues = existing_issues(memory, mount, prefix, &incoming, &[])?;
    apply(
        memory,
        mount,
        vec![change],
        write,
        json!({"operation":"collection", "path":path,"definition":incoming,"issues":issues}),
    )
}
