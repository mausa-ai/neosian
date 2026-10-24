//! Deterministic, keyless upkeep. Operators pause other writers: wire 6
//! has no conditional delete, and a whole maintenance pass is not atomic.

use std::collections::{BTreeMap, BTreeSet};
use std::io::Write;

use serde_json::{Value, json};

use super::dispatch::Memory;
use super::mounts::{Mount, state_path};
use crate::store::error::Error;
use crate::store::time::Stamp;
use crate::store::{Document, Entry};

struct Action<'a> {
    mount: &'a Mount,
    entry: Entry,
    content: String,
    keeper: Option<String>,
}

fn plan(memory: &Memory, cutoff: Stamp) -> Result<Vec<Action<'_>>, Error> {
    let mut actions = Vec::new();
    let mut scopes = BTreeSet::new();
    for mount in &memory.mounts {
        if mount.read_only || mount.edit_only || !scopes.insert(&mount.scope) {
            continue;
        }
        let mut groups: BTreeMap<String, Vec<Entry>> = BTreeMap::new();
        for entry in memory.store.list_documents(&mount.scope, "")? {
            if entry.redacted || state_path(&entry.path) {
                continue;
            }
            let Some(document) = memory.store.read(&mount.scope, &entry.path)? else {
                continue;
            };
            if document.envelope.redacted || document.envelope.version != entry.version {
                continue;
            }
            if document.content.trim().is_empty() {
                if entry.updated_at.instant() <= cutoff.instant() {
                    actions.push(Action {
                        mount,
                        entry,
                        content: document.content,
                        keeper: None,
                    });
                }
            } else {
                groups.entry(document.content).or_default().push(entry);
            }
        }
        for (content, mut entries) in groups {
            entries.sort_by(|a, b| {
                a.created_at
                    .instant()
                    .cmp(&b.created_at.instant())
                    .then(a.path.cmp(&b.path))
            });
            let keeper = entries[0].path.clone();
            for entry in entries
                .into_iter()
                .skip(1)
                .filter(|entry| entry.updated_at.instant() <= cutoff.instant())
            {
                actions.push(Action {
                    mount,
                    entry,
                    content: content.clone(),
                    keeper: Some(keeper.clone()),
                });
            }
        }
    }
    Ok(actions)
}

fn unchanged(document: &Document, action: &Action<'_>) -> bool {
    !document.envelope.redacted
        && document.envelope.version == action.entry.version
        && document.content == action.content
}

pub fn run(
    memory: &Memory,
    days: u32,
    dry_run: bool,
    json: bool,
    out: &mut impl Write,
) -> Result<u8, Error> {
    let cutoff = Stamp::now().after(-i64::from(days) * 86400);
    // Parse the entire selection before performing the first deletion.
    let actions = plan(memory, cutoff)?;
    let mut planned = Vec::new();
    let mut writes: Vec<Value> = Vec::new();
    for action in &actions {
        let path = format!("/{}/{}", action.mount.path, action.entry.path);
        planned.push(json!({"path": path, "version": action.entry.version,
            "reason": if action.keeper.is_some() { "duplicate" } else { "empty" },
            "keeper": action.keeper.as_ref().map(|p| format!("/{}/{p}", action.mount.path))}));
        if dry_run {
            continue;
        }
        let Some(current) = memory.store.read(&action.mount.scope, &action.entry.path)? else {
            continue;
        };
        if !unchanged(&current, action) {
            continue;
        }
        if let Some(keeper) = &action.keeper {
            let kept = memory.store.read(&action.mount.scope, keeper)?;
            if !kept.is_some_and(|doc| !doc.envelope.redacted && doc.content == action.content) {
                continue;
            }
        }
        if memory
            .store
            .delete(&action.mount.scope, &action.entry.path, &memory.actor)?
            .removed
        {
            writes.push(json!({"command": "delete", "path": path, "version": null}));
        }
    }
    if json {
        writeln!(
            out,
            "{}",
            json!({"dry_run": dry_run, "actions": planned, "writes": writes})
        )?;
    } else if dry_run {
        for action in &planned {
            writeln!(
                out,
                "would delete {} ({})",
                action["path"].as_str().unwrap(),
                action["reason"].as_str().unwrap()
            )?;
        }
        if planned.is_empty() {
            writeln!(out, "nothing to do")?;
        }
    } else {
        for write in &writes {
            writeln!(out, "delete {}", write["path"].as_str().unwrap())?;
        }
        if writes.is_empty() {
            writeln!(out, "nothing to do")?;
        }
    }
    Ok(0)
}
