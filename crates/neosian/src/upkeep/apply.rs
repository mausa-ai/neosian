//! Recheck an approved proposal; acknowledge partial writes without inventing rollback.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{Value, json};

use crate::knowledge::model::{Content, invalid};
use crate::memory::dispatch::Memory;
use crate::memory::mounts::{structural, writable};
use crate::store::{Document, error::Error};

use super::model::{Guard, Operation, Proposal};
use super::prepare::{check_rules, deletion, evidence, historical, rules_path};
use super::{address, current, observe, same, source, text_error, workspace};

pub(super) fn bindings(memory: &Memory, proposal: &Proposal) -> Result<(), Error> {
    proposal.validate()?;
    if proposal.workspace != workspace(memory) {
        return Err(invalid(
            "proposal belongs to a different workspace selection",
        ));
    }
    let mut identities = BTreeSet::new();
    for guard in &proposal.observations {
        let (mount, name) = address(memory, &guard.path, true)?;
        if mount.scope != guard.scope || source(memory, &mount.scope) != guard.source {
            return Err(invalid("proposal mount/source/scope binding changed"));
        }
        if !identities.insert((mount.scope.to_ascii_lowercase(), name.to_ascii_lowercase())) {
            return Err(invalid("aliased or case-colliding upkeep observations"));
        }
    }
    for change in &proposal.changes {
        address(memory, &change.path, false)?;
    }
    Ok(())
}

fn recheck(memory: &Memory, expected: &BTreeMap<String, Guard>) -> Result<(), Error> {
    for (path, expected) in expected {
        let (actual, _) = observe(memory, path)?;
        if !same(expected, &actual) {
            return Err(invalid(format!(
                "upkeep observation changed: {path}; inspect and prepare a fresh proposal"
            )));
        }
    }
    Ok(())
}

fn preflight(memory: &Memory, proposal: &Proposal) -> Result<(), Error> {
    bindings(memory, proposal)?;
    let expected = proposal
        .observations
        .iter()
        .map(|g| (g.path.clone(), g.clone()))
        .collect();
    recheck(memory, &expected)?;
    for guard in &proposal.observations {
        let doc = observe(memory, &guard.path)?.1;
        if doc.as_ref().is_some_and(|d| d.envelope.redacted)
            || (doc.is_none() && !guard.path.ends_with("/.collection"))
        {
            return Err(invalid("upkeep evidence is missing or redacted"));
        }
    }
    for change in &proposal.changes {
        let (mount, _) = address(memory, &change.path, false)?;
        writable(mount)?;
        let doc = current(observe(memory, &change.path)?.1)?;
        if change.operation == Operation::Delete {
            structural(mount)?;
            deletion(memory, &change.path, &doc, change.keeper.as_deref())?;
        } else {
            let content = Content::parse(
                change.content.as_deref().expect("validated content"),
                "knowledge",
            )?;
            let old = Content::parse(&doc.content, "knowledge")?;
            if old
                .metadata
                .sources
                .iter()
                .any(|s| !content.metadata.sources.contains(s))
            {
                return Err(invalid(
                    "upkeep replacement must preserve existing source provenance",
                ));
            }
            let origin = evidence(memory, &change.path, &doc, &change.reason)?;
            if !content.metadata.sources.contains(&origin) {
                return Err(invalid(
                    "replacement must retain its observed target as a source",
                ));
            }
            let rules = rules_path(memory, &change.path, &content)?;
            if !proposal.observations.iter().any(|g| g.path == rules) {
                return Err(invalid(
                    "replacement requires a collection-rule observation",
                ));
            }
            check_rules(observe(memory, &rules)?.1.as_ref(), &content)?;
            if let Some(version) = change.restore_version {
                let raw = historical(memory, &change.path, version)?;
                let (_, name) = address(memory, &change.path, false)?;
                let fallback = name.rsplit_once('/').map_or("knowledge", |(p, _)| p);
                let restored = Content::parse(&raw, fallback)?;
                if restored.body != content.body
                    || restored.metadata.collection != content.metadata.collection
                    || restored.metadata.attributes != content.metadata.attributes
                    || restored.metadata.references != content.metadata.references
                    || restored
                        .metadata
                        .sources
                        .iter()
                        .any(|s| !content.metadata.sources.contains(s))
                {
                    return Err(invalid("restoration differs from the retained revision"));
                }
            }
        }
    }
    Ok(())
}

fn diff(before: &str, after: &str) -> Value {
    let a: Vec<_> = before.split_inclusive('\n').collect();
    let b: Vec<_> = after.split_inclusive('\n').collect();
    let start = a.iter().zip(&b).take_while(|(a, b)| a == b).count();
    let end = a[start..]
        .iter()
        .rev()
        .zip(b[start..].iter().rev())
        .take_while(|(a, b)| a == b)
        .count();
    let removed = a[start..a.len() - end].concat();
    let added = b[start..b.len() - end].concat();
    json!({"common_prefix_lines":start, "removed":removed.chars().take(4096).collect::<String>(),
        "added":added.chars().take(4096).collect::<String>(),
        "truncated":removed.chars().count() > 4096 || added.chars().count() > 4096})
}

pub fn inspect(memory: &Memory, proposal: &Proposal) -> Result<Value, Error> {
    bindings(memory, proposal)?;
    let mut observations = vec![];
    for guard in &proposal.observations {
        let (actual, doc) = observe(memory, &guard.path)?;
        let change = proposal.changes.iter().find(|c| c.path == guard.path);
        let state = if doc.as_ref().is_some_and(|d| d.envelope.redacted) {
            "redacted"
        } else if same(guard, &actual) {
            "matches_before"
        } else if let Some(doc) = &doc {
            if change.and_then(|c| c.content.as_deref()) == Some(&doc.content) {
                "matches_proposed_content"
            } else {
                "changed"
            }
        } else {
            "missing"
        };
        observations.push(json!({"path":guard.path,"state":state,"expected_version":guard.version,
            "current_version":actual.version,"actor":doc.as_ref().and_then(|d| d.envelope.actor_value())}));
    }
    Ok(json!({"proposal_id":proposal.id,"observations":observations}))
}

fn write_one(
    memory: &Memory,
    proposal: &Proposal,
    index: usize,
    doc: &Document,
) -> Result<Value, Error> {
    let change = &proposal.changes[index];
    let (mount, path) = address(memory, &change.path, false)?;
    writable(mount)?;
    if let Some(content) = &change.content {
        let version = memory.store.write(
            &mount.scope,
            path,
            content,
            &memory.actor,
            Some(doc.envelope.version),
        )?;
        Ok(json!({"path":change.path,"operation":change.operation,"version":version}))
    } else {
        structural(mount)?;
        deletion(memory, &change.path, doc, change.keeper.as_deref())?;
        let deleted = memory.store.delete(&mount.scope, path, &memory.actor)?;
        if !deleted.removed {
            return Err(invalid("document disappeared before upkeep deletion"));
        }
        Ok(json!({"path":change.path,"operation":"delete","version":deleted.version}))
    }
}

pub fn apply(
    memory: &Memory,
    proposal: &Proposal,
    write: bool,
    approval: Option<&str>,
) -> Result<(Value, u8), Error> {
    if (write && approval != Some(proposal.id.as_str())) || (!write && approval.is_some()) {
        return Err(invalid(
            "write requires the exact proposal approval digest; preview takes no approval",
        ));
    }
    preflight(memory, proposal)?;
    let mut changes = vec![];
    for change in &proposal.changes {
        let doc = current(observe(memory, &change.path)?.1)?;
        changes.push(json!({"path":change.path,"operation":change.operation,"reason":change.reason,
            "expected_version":doc.envelope.version,"keeper":change.keeper,"restore_version":change.restore_version,
            "diff":diff(&doc.content,change.content.as_deref().unwrap_or(""))}));
    }
    let mut report = json!({"success":true,"preview":!write,"proposal_id":proposal.id,"changes":changes,"applied":[]});
    if !write {
        let expected = proposal
            .observations
            .iter()
            .map(|g| (g.path.clone(), g.clone()))
            .collect();
        recheck(memory, &expected)?;
        return Ok((report, 0));
    }
    let mut expected = proposal
        .observations
        .iter()
        .map(|g| (g.path.clone(), g.clone()))
        .collect();
    let result = (|| -> Result<(), Error> {
        for (index, change) in proposal.changes.iter().enumerate() {
            recheck(memory, &expected)?;
            let (before, doc) = observe(memory, &change.path)?;
            if !same(
                expected.get(&change.path).expect("target observation"),
                &before,
            ) {
                return Err(invalid("upkeep target changed immediately before write"));
            }
            let doc = current(doc)?;
            let receipt = write_one(memory, proposal, index, &doc)?;
            report["applied"]
                .as_array_mut()
                .expect("receipts")
                .push(receipt.clone());
            let (guard, after) = observe(memory, &change.path)?;
            match (&change.content, after) {
                (Some(content), Some(after))
                    if !after.envelope.redacted
                        && after.content == *content
                        && Some(after.envelope.version) == receipt["version"].as_u64() => {}
                (None, None) => {}
                _ => return Err(invalid("state changed after an acknowledged upkeep write")),
            }
            expected.insert(change.path.clone(), guard);
        }
        Ok(())
    })();
    if let Err(error) = result {
        report["success"] = false.into();
        report["error"] = text_error(error).into();
        return Ok((report, 1));
    }
    Ok((report, 0))
}
