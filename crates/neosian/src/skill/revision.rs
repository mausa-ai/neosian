//! Bounded, reviewable root edits and recovery. No deleted/erased identity is revived.

use serde_json::{Value, json};

use super::model::{address, parse};
use crate::knowledge::input::{Input, Item};
use crate::knowledge::model::{MAX_BODY, Source, digest, invalid};
use crate::memory::dispatch::Memory;
use crate::memory::mounts::resolve;
use crate::store::error::Error;
use crate::store::journal::Action;

fn historical(memory: &Memory, path: &str, version: u64) -> Result<String, Error> {
    let (mount, document) = resolve(&memory.mounts, path)?;
    let row = memory
        .store
        .versions(&mount.scope, document, 1000)?
        .into_iter()
        .find(|r| r.version == version)
        .ok_or_else(|| invalid("revision is unavailable in the most recent 1000 rows"))?;
    if row.redacted || row.action == Action::Deleted || row.content.len() > MAX_BODY {
        return Err(invalid("revision is erased, deleted or oversized"));
    }
    Ok(row.content)
}

pub fn edit(
    memory: &Memory,
    path: &str,
    raw: Option<&str>,
    revision: Option<u64>,
    expected: u64,
    write: bool,
) -> Result<(Value, u8), Error> {
    let (_, name) = address(path)?;
    let doc = super::current(memory, path)?;
    if doc.envelope.version != expected {
        return Err(invalid(
            "current skill version differs from --expect-version",
        ));
    }
    let mut content = if let Some(version) = revision {
        parse(&historical(memory, path, version)?)?.0
    } else {
        let raw = raw.ok_or_else(|| invalid("edit requires a file"))?;
        if raw.starts_with("<!-- neosian-knowledge:") {
            parse(raw)?.0
        } else {
            let mut old = parse(&doc.content)?.0;
            old.body = raw.into();
            old
        }
    };
    if let Some(raw) = raw {
        let source = Source {
            label: "skill-edit".into(),
            file: "SKILL.md".into(),
            sha256: digest(raw),
            origin: None,
        };
        if !content.metadata.sources.contains(&source) {
            content.metadata.sources.push(source);
        }
    }
    let input = Input {
        collection: None,
        items: vec![Item {
            path: name.into(),
            content,
        }],
        ignored: vec![],
    };
    let (mut report, code) = super::package::import(memory, path, input, true, write)?;
    report["operation"] = json!(if revision.is_some() { "revert" } else { "edit" });
    report["expected_version"] = json!(expected);
    if let Some(version) = revision {
        report["restored_from"] = json!(version);
    }
    Ok((report, code))
}

pub fn diff(memory: &Memory, path: &str, from: u64, to: Option<u64>) -> Result<(Value, u8), Error> {
    address(path)?;
    let doc = super::current(memory, path)?;
    let before = historical(memory, path, from)?;
    let after = match to {
        Some(v) => historical(memory, path, v)?,
        None => doc.content,
    };
    let a: Vec<_> = before.split_inclusive('\n').collect();
    let b: Vec<_> = after.split_inclusive('\n').collect();
    let start = a.iter().zip(&b).take_while(|(a, b)| a == b).count();
    let end = a[start..]
        .iter()
        .rev()
        .zip(b[start..].iter().rev())
        .take_while(|(a, b)| a == b)
        .count();
    Ok((
        json!({"path":path,"from_version":from,"to_version":to.unwrap_or(doc.envelope.version),"changed":before != after,
        "common_prefix_lines":start,"removed":a[start..a.len()-end].concat(),"added":b[start..b.len()-end].concat()}),
        0,
    ))
}
