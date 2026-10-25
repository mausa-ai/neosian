//! Preflight whole batches; receipts make partial failures explicit.

use serde_json::{Value, json};

use super::input::{Input, Item, Staged};
use super::model::{DEFINITION, invalid, ordinary};
use super::rules::{Definition, Issue};
use super::{definition, snapshot, target, text_error};
use crate::limits::{Knowledge, Scratch};
use crate::memory::dispatch::Memory;
use crate::memory::mounts::{Mount, structural, writable};
use crate::state::scan::Entries;
use crate::store::{Document, error::Error};
use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

struct Change {
    path: String,
    content: PathBuf,
    before: Option<String>,
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

fn fingerprint(scope: &str, path: &str, doc: Option<&Document>) -> Result<Option<String>, Error> {
    Ok(snapshot(scope, path, doc)?.map(|mut v| {
        v.sort_all_objects();
        super::model::digest(serde_json::to_vec(&v).expect("snapshot serializes"))
    }))
}

fn change(
    memory: &Memory,
    mount: &Mount,
    path: String,
    content: String,
    conflict: &str,
    scratch: &Scratch,
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
        before: fingerprint(&mount.scope, &path, found.as_ref())?,
        path,
        content: scratch.save(&content)?,
        action,
    })
}

fn case_keys(path: &str) -> impl Iterator<Item = (&str, String)> {
    path.match_indices('/')
        .map(|(i, _)| &path[..i])
        .chain(std::iter::once(path))
        .map(|p| (p, p.to_ascii_lowercase()))
}

fn existing_issues(
    memory: &Memory,
    mount: &Mount,
    prefix: &str,
    rules: &Definition,
    planned: &[Change],
) -> Result<Vec<Issue>, Error> {
    let selected = format!("{prefix}/");
    let entries = Entries::new(memory.store.as_ref(), &mount.scope, &selected);
    let limits = Knowledge::load()?;
    let replaced: BTreeSet<_> = planned
        .iter()
        .filter(|p| matches!(p.action, "create" | "replace"))
        .map(|p| p.path.as_str())
        .collect();
    let mut issues = vec![];
    let mut total = 0;
    let mut count = 0;
    for entry in entries {
        let entry = entry?;
        if !entry.path.starts_with(&format!("{prefix}/")) {
            return Err(invalid(
                "backend returned a document outside the selected collection",
            ));
        }
        if entry.path == format!("{prefix}/{DEFINITION}") || entry.redacted {
            continue;
        }
        count += 1;
        limits.check(count, total)?;
        if entry.path.ends_with(&format!("/{DEFINITION}")) {
            return Err(invalid(
                "nested collections require separate definition changes",
            ));
        }
        if replaced.contains(entry.path.as_str()) {
            continue;
        }
        if let Some(doc) = memory.store.read(&mount.scope, &entry.path)? {
            if doc.envelope.redacted {
                continue;
            }
            total += doc.content.len();
            limits.check(count, total)?;
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
    let mut planned = BTreeMap::new();
    for change in &changes {
        for (spelling, key) in case_keys(&change.path) {
            if planned
                .get(&key)
                .is_some_and(|previous| *previous != spelling)
            {
                return Err(invalid(format!(
                    "case-colliding destination: {}",
                    change.path
                )));
            }
            planned.insert(key, spelling);
        }
    }
    for entry in Entries::new(memory.store.as_ref(), &mount.scope, "") {
        let entry = entry?;
        if case_keys(&entry.path).any(|(spelling, key)| {
            planned
                .get(&key)
                .is_some_and(|previous| *previous != spelling)
        }) {
            return Err(invalid(format!(
                "case-colliding destination: {}",
                entry.path
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
            if fingerprint(&mount.scope, &change.path, current.as_ref())? != change.before {
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
            if fingerprint(&mount.scope, &change.path, current.as_ref())? != change.before {
                return Err(invalid(format!(
                    "destination changed before write: {}",
                    change.path
                )));
            }
            let version = memory.store.write(
                &mount.scope,
                &change.path,
                &super::input::read(&change.content, super::model::MAX_BODY)?,
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

struct Prepared<'a> {
    collection: Option<Definition>,
    items: Box<dyn Iterator<Item = Result<Item, Error>> + 'a>,
    ignored: Vec<String>,
    scratch: &'a Scratch,
}

pub fn import(
    memory: &Memory,
    path: &str,
    input: Input,
    conflict: &str,
    write: bool,
) -> Result<(Value, u8), Error> {
    let scratch = Scratch::new()?;
    import_items(
        memory,
        path,
        Prepared {
            collection: input.collection,
            items: Box::new(input.items.into_iter().map(Ok)),
            ignored: input.ignored,
            scratch: &scratch,
        },
        conflict,
        write,
    )
}

pub fn import_staged(
    memory: &Memory,
    path: &str,
    input: Staged,
    conflict: &str,
    write: bool,
) -> Result<(Value, u8), Error> {
    let (_, prefix) = target(memory, path)?;
    let items = input.items.iter().map(|(path, file)| {
        Ok(Item {
            path: path.clone(),
            content: super::model::Content::parse(
                &super::input::read(file, super::model::MAX_BODY)?,
                prefix,
            )?,
        })
    });
    import_items(
        memory,
        path,
        Prepared {
            collection: input.collection.clone(),
            items: Box::new(items),
            ignored: input.ignored.clone(),
            scratch: &input.scratch,
        },
        conflict,
        write,
    )
}

fn import_items(
    memory: &Memory,
    path: &str,
    input: Prepared<'_>,
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
            input.scratch,
        )?);
    }
    let mut issues = vec![];
    let mut checked_parents = BTreeSet::new();
    for item in input.items {
        let item = item?;
        let target = format!("{prefix}/{}", item.path);
        ordinary(&target)?;
        let planned = change(
            memory,
            mount,
            target.clone(),
            item.content.render()?,
            conflict,
            input.scratch,
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
                if checked_parents.insert(nested.clone())
                    && memory
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
    let scratch = Scratch::new()?;
    let change = change(
        memory,
        mount,
        format!("{prefix}/{DEFINITION}"),
        incoming.render(),
        "replace",
        &scratch,
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
