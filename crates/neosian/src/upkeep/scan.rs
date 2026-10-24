//! Findings are review candidates, never conclusions that an old fact is false.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{Value, json};

use crate::knowledge::model::{Content, MAX_BODY, digest, invalid, ordinary};
use crate::memory::dispatch::Memory;
use crate::memory::mounts::resolve;
use crate::store::{error::Error, time::Stamp};

use super::model::{MAX_SCAN, MAX_SCAN_BYTES};
use super::prepare::{check_rules, rules_path};
use super::{observe, source, text_error};

pub fn scan(memory: &Memory, path: &str, limit: usize, stale_days: u32) -> Result<Value, Error> {
    if !(1..=MAX_SCAN).contains(&limit)
        || !(1..=365_000).contains(&stale_days)
        || !path.starts_with('/')
    {
        return Err(invalid(
            "scan requires an absolute path, limit 1..1000 and stale_days 1..365000",
        ));
    }
    let selected = if path == "/" {
        None
    } else {
        Some(resolve(&memory.mounts, path)?)
    };
    if let Some((_, prefix)) = selected
        && !prefix.is_empty()
    {
        ordinary(prefix)?;
    }
    let mut mounts: Vec<_> = memory
        .mounts
        .iter()
        .filter(|m| selected.is_none_or(|(chosen, _)| m.path == chosen.path))
        .collect();
    mounts.sort_by(|a, b| a.path.cmp(&b.path));
    let mut findings = vec![];
    let (mut checked, mut bytes, mut truncated) = (0, 0, false);
    let now = Stamp::now();
    let mut scopes = BTreeSet::new();
    for mount in mounts {
        if !scopes.insert(mount.scope.clone()) {
            continue;
        }
        let prefix = selected.map_or("", |(_, p)| p);
        let mut entries = memory.store.list_documents(&mount.scope, prefix)?;
        if entries.len() > 10_000 {
            return Err(invalid(
                "upkeep scan listing exceeds 10000 entries; select a narrower prefix",
            ));
        }
        if entries.iter().any(|e| !e.path.starts_with(prefix)) {
            return Err(invalid(
                "backend returned a document outside the scan prefix",
            ));
        }
        entries.sort_by(|a, b| {
            a.created_at
                .instant()
                .cmp(&b.created_at.instant())
                .then(a.path.cmp(&b.path))
        });
        let mut duplicates: BTreeMap<String, (String, String)> = BTreeMap::new();
        for entry in entries {
            if entry.redacted || ordinary(&entry.path).is_err() {
                continue;
            }
            if checked == limit {
                truncated = true;
                break;
            }
            let Some(doc) = memory.store.read(&mount.scope, &entry.path)? else {
                continue;
            };
            if doc.envelope.redacted {
                continue;
            }
            checked += 1;
            bytes += doc.content.len();
            if bytes > MAX_SCAN_BYTES {
                return Err(invalid("upkeep scan exceeds 8 MiB; select fewer documents"));
            }
            let path = format!("/{}/{}", mount.path, entry.path);
            let eligible = !mount.read_only
                && !mount.edit_only
                && doc.content.len() <= MAX_BODY
                && doc.envelope.updated_at.instant() <= now.after(-7 * 86400).instant();
            let mut issues = vec![];
            if doc.content.trim().is_empty() {
                issues.push(json!({"kind":"empty","eligible_for_deletion":eligible}));
            } else {
                let hash = digest(&doc.content);
                if let Some((keeper, kept)) = duplicates.get(&hash) {
                    if kept == &doc.content {
                        issues.push(json!({"kind":"duplicate","keeper":keeper,"eligible_for_deletion":eligible}));
                    }
                } else {
                    duplicates.insert(hash, (path.clone(), doc.content.clone()));
                }
            }
            if doc.envelope.updated_at.instant()
                <= now.after(-i64::from(stale_days) * 86400).instant()
            {
                issues.push(
                    json!({"kind":"stale_review","updated_at":doc.envelope.updated_at.utc().z()}),
                );
            }
            if doc.content.len() > MAX_BODY {
                issues.push(json!({"kind":"oversized","bytes":doc.content.len()}));
            } else {
                let fallback = entry.path.rsplit_once('/').map_or("knowledge", |(p, _)| p);
                match Content::parse(&doc.content, fallback) {
                    Err(error) => {
                        issues.push(json!({"kind":"invalid_structure","detail":text_error(error)}))
                    }
                    Ok(content) => {
                        let rules = observe(memory, &rules_path(memory, &path, &content)?)?.1;
                        if let Err(error) = check_rules(rules.as_ref(), &content) {
                            issues.push(
                                json!({"kind":"invalid_structure","detail":text_error(error)}),
                            );
                        }
                    }
                }
            }
            if !issues.is_empty() {
                findings.push(json!({"path":path,"scope":mount.scope,"source":source(memory,&mount.scope),
                    "version":doc.envelope.version,"content_sha256":digest(&doc.content),"issues":issues}));
            }
        }
    }
    Ok(json!({"checked":checked,"truncated":truncated,"findings":findings,"stale_days":stale_days}))
}
