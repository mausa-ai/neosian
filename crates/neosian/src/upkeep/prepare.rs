//! Prepare content and source guards without applying any change.

use std::collections::{BTreeMap, BTreeSet};

use crate::knowledge::model::{Content, DEFINITION, Origin, Source, digest, invalid};
use crate::knowledge::rules::Definition;
use crate::memory::dispatch::Memory;
use crate::memory::mounts::{structural, writable};
use crate::store::{Document, error::Error, journal::Action, time::Stamp};

use super::model::{Change, Edit, Guard, Operation, Proposal, Request};
use super::{address, current, observe, same, workspace};

fn record(
    memory: &Memory,
    path: &str,
    guards: &mut BTreeMap<String, Guard>,
) -> Result<Option<Document>, Error> {
    let (guard, doc) = observe(memory, path)?;
    if guards.get(path).is_some_and(|prior| !same(prior, &guard)) {
        return Err(invalid(
            "source changed while preparing upkeep; start again",
        ));
    }
    guards.insert(path.into(), guard);
    Ok(doc)
}

pub(super) fn historical(memory: &Memory, path: &str, version: u64) -> Result<String, Error> {
    let (mount, name) = address(memory, path, false)?;
    let row = memory
        .store
        .versions(&mount.scope, name, 1000)?
        .into_iter()
        .find(|r| r.version == version)
        .ok_or_else(|| invalid("revision is not among the latest 1000 rows"))?;
    if row.redacted || row.action == Action::Deleted {
        return Err(invalid(
            "an erased or deleted revision cannot supply replacement content",
        ));
    }
    Ok(row.content)
}

pub(super) fn deletion(
    memory: &Memory,
    path: &str,
    doc: &Document,
    keeper: Option<&str>,
) -> Result<(), Error> {
    if doc.envelope.updated_at.instant() > Stamp::now().after(-7 * 86400).instant() {
        return Err(invalid(
            "upkeep deletion requires a document at least seven days old",
        ));
    }
    match keeper {
        None if doc.content.trim().is_empty() => Ok(()),
        Some(keeper) if keeper != path => {
            let (mount, name) = address(memory, path, false)?;
            let (kept, kept_name) = address(memory, keeper, false)?;
            if mount.scope != kept.scope || source_key(memory, mount) != source_key(memory, kept) {
                return Err(invalid(
                    "duplicate keeper must belong to the same source and scope",
                ));
            }
            if name.eq_ignore_ascii_case(kept_name) {
                return Err(invalid("a duplicate keeper cannot alias its target"));
            }
            let other = current(observe(memory, keeper)?.1)?;
            if other.content == doc.content && !other.content.trim().is_empty() {
                Ok(())
            } else {
                Err(invalid(
                    "duplicate keeper does not have the exact current content",
                ))
            }
        }
        _ => Err(invalid(
            "only empty documents or exact duplicates with a keeper can be deleted",
        )),
    }
}

fn source_key(memory: &Memory, mount: &crate::memory::mounts::Mount) -> Option<String> {
    super::source(memory, &mount.scope)
}

pub(super) fn evidence(
    memory: &Memory,
    path: &str,
    doc: &Document,
    reason: &str,
) -> Result<Source, Error> {
    let (mount, name) = address(memory, path, false)?;
    let extra = crate::wire::encode::extra_json(&doc.envelope, &mount.scope, name)?;
    Ok(Source {
        label: format!("upkeep: {reason}"),
        file: name.into(),
        sha256: digest(&doc.content),
        origin: Some(Origin {
            scope: mount.scope.clone(),
            path: name.into(),
            version: doc.envelope.version,
            created_at: doc.envelope.created_at.utc().z(),
            updated_at: doc.envelope.updated_at.utc().z(),
            actor: doc.envelope.actor_value(),
            extra: extra.as_object().expect("extra object").clone(),
        }),
    })
}

pub(super) fn rules_path(memory: &Memory, path: &str, content: &Content) -> Result<String, Error> {
    let (mount, _) = address(memory, path, false)?;
    Ok(format!(
        "/{}/{}/{DEFINITION}",
        mount.path, content.metadata.collection
    ))
}

pub(super) fn check_rules(doc: Option<&Document>, content: &Content) -> Result<(), Error> {
    if let Some(doc) = doc {
        if doc.envelope.redacted {
            return Err(invalid("collection definition is redacted"));
        }
        let rules = Definition::parse(&doc.content)?;
        let issues = rules.check("document", &content.metadata.attributes);
        if !issues.is_empty() {
            return Err(invalid(format!(
                "replacement violates collection rules: {}",
                serde_json::to_string(&issues).expect("issues serialize")
            )));
        }
    }
    Ok(())
}

fn replacement(
    memory: &Memory,
    edit: &Edit,
    doc: &Document,
    guards: &mut BTreeMap<String, Guard>,
) -> Result<String, Error> {
    let (_, name) = address(memory, &edit.path, false)?;
    let fallback = name.rsplit_once('/').map_or("knowledge", |(p, _)| p);
    let old = Content::parse(&doc.content, fallback)?;
    let mut content = if let Some(version) = edit.restore_version {
        let raw = historical(memory, &edit.path, version)?;
        let mut restored = Content::parse(&raw, fallback)?;
        for source in &old.metadata.sources {
            if !restored.metadata.sources.contains(source) {
                restored.metadata.sources.push(source.clone());
            }
        }
        restored.metadata.sources.push(Source {
            label: format!(
                "upkeep restore v{version}: {}",
                edit.reason.chars().take(200).collect::<String>()
            ),
            file: name.into(),
            sha256: digest(&raw),
            origin: None,
        });
        restored
    } else {
        old
    };
    if let Some(body) = &edit.body {
        if body.starts_with("<!-- neosian-knowledge:") {
            return Err(invalid(
                "replacement body excludes the knowledge header; use attributes/references fields",
            ));
        }
        content.body.clone_from(body);
    }
    if let Some(attributes) = &edit.attributes {
        content.metadata.attributes.extend(attributes.clone());
    }
    if let Some(references) = &edit.references {
        content.metadata.references.clone_from(references);
    }
    let mut sources = vec![evidence(memory, &edit.path, doc, &edit.reason)?];
    for path in &edit.sources {
        address(memory, path, false)?;
        let doc = current(record(memory, path, guards)?)?;
        sources.push(evidence(memory, path, &doc, &edit.reason)?);
    }
    for source in sources {
        if !content.metadata.sources.contains(&source) {
            content.metadata.sources.push(source);
        }
    }
    let rules = record(memory, &rules_path(memory, &edit.path, &content)?, guards)?;
    check_rules(rules.as_ref(), &content)?;
    let rendered = content.render()?;
    Content::parse(&rendered, fallback)?;
    Ok(rendered)
}

pub fn prepare(memory: &Memory, request: &Request) -> Result<Proposal, Error> {
    request.validate()?;
    let mut observations = BTreeMap::new();
    let mut identities = BTreeSet::new();
    let mut changes = vec![];
    for edit in &request.changes {
        let (mount, name) = address(memory, &edit.path, false)?;
        writable(mount)?;
        if !identities.insert((mount.scope.to_ascii_lowercase(), name.to_ascii_lowercase())) {
            return Err(invalid("aliased or case-colliding upkeep targets"));
        }
        let doc = current(record(memory, &edit.path, &mut observations)?)?;
        if doc.envelope.version != edit.expected_version {
            return Err(invalid("upkeep target differs from expected_version"));
        }
        let content = if edit.operation == Operation::Delete {
            structural(mount)?;
            if let Some(keeper) = &edit.keeper {
                address(memory, keeper, false)?;
                current(record(memory, keeper, &mut observations)?)?;
            }
            deletion(memory, &edit.path, &doc, edit.keeper.as_deref())?;
            None
        } else {
            Some(replacement(memory, edit, &doc, &mut observations)?)
        };
        changes.push(Change {
            path: edit.path.clone(),
            operation: edit.operation.clone(),
            reason: edit.reason.clone(),
            content,
            keeper: edit.keeper.clone(),
            restore_version: edit.restore_version,
        });
    }
    let mut proposal = Proposal {
        format: 1,
        workspace: workspace(memory),
        observations: observations.into_values().collect(),
        changes,
        id: String::new(),
    };
    proposal.id = proposal.digest();
    super::apply::bindings(memory, &proposal)?;
    Ok(proposal)
}
