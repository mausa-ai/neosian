//! Current collection checks, permission-bounded references and portable bundles.

use std::path::Path;

use serde_json::{Value, json};

use super::input::{Bundle, Record};
use super::model::{
    Content, DEFINITION, MAX_BODY, MAX_DOCUMENTS, MAX_TOTAL, Origin, Source, digest, invalid,
    ordinary,
};
use super::rules::Issue;
use super::{definition, target};
use crate::memory::dispatch::Memory;
use crate::memory::mounts::Mount;
use crate::store::{Document, error::Error, fileio};

fn documents(
    memory: &Memory,
    mount: &Mount,
    prefix: &str,
) -> Result<Vec<(String, Document)>, Error> {
    let entries = memory
        .store
        .list_documents(&mount.scope, &format!("{prefix}/"))?;
    if entries.len() > MAX_DOCUMENTS + 1 {
        return Err(invalid("collection exceeds 1000 documents"));
    }
    let mut result = vec![];
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
                "nested collections must be checked or exported separately",
            ));
        }
        ordinary(&entry.path)?;
        if let Some(document) = memory.store.read(&mount.scope, &entry.path)? {
            if document.envelope.redacted {
                continue;
            }
            total += document.content.len();
            if document.content.len() > MAX_BODY || total > MAX_TOTAL {
                return Err(invalid(
                    "collection exceeds its document or total byte limit",
                ));
            }
            result.push((entry.path, document));
        }
    }
    result.sort_by(|a, b| a.0.cmp(&b.0));
    Ok(result)
}

pub fn validate(memory: &Memory, path: &str) -> Result<(Value, u8), Error> {
    let (mount, prefix) = target(memory, path)?;
    let definition = definition(memory, mount, prefix)?;
    let documents = documents(memory, mount, prefix)?;
    let mut issues = vec![];
    for (path, document) in &documents {
        match Content::parse(&document.content, prefix) {
            Ok(content) => {
                if content.metadata.collection != prefix {
                    issues.push(Issue {
                        path: path.clone(),
                        field: "collection".into(),
                        message: "document names a different collection".into(),
                    });
                }
                if let Some(definition) = &definition {
                    issues.extend(definition.check(path, &content.metadata.attributes));
                }
            }
            Err(error) => issues.push(Issue {
                path: path.clone(),
                field: "metadata".into(),
                message: super::text_error(error),
            }),
        }
    }
    let code = u8::from(!issues.is_empty());
    Ok((
        json!({"success":code == 0,"path":path,"checked":documents.len(),"schema_enabled":definition.is_some_and(|d| d.schema.is_some()),"issues":issues}),
        code,
    ))
}

pub fn references(memory: &Memory, path: &str) -> Result<(Value, u8), Error> {
    let (mount, path) = target(memory, path)?;
    let found = memory
        .store
        .read(&mount.scope, path)?
        .filter(|d| !d.envelope.redacted)
        .ok_or_else(|| invalid("reference source is absent or redacted"))?;
    let fallback = path
        .rsplit_once('/')
        .map_or("knowledge", |(parent, _)| parent);
    let content = Content::parse(&found.content, fallback)?;
    let mut results = vec![];
    for reference in content.metadata.references {
        let (scope, target) = match reference.scope {
            Some(scope) => (scope, reference.path),
            None => (
                mount.scope.clone(),
                format!("{}/{}", content.metadata.collection, reference.path),
            ),
        };
        let selected = memory.mounts.iter().find(|m| m.scope == scope);
        let resolved = selected.map(|_| memory.store.read(&scope, &target));
        let (status, version) = match resolved {
            Some(Ok(Some(doc))) if !doc.envelope.redacted => {
                ("available", Some(doc.envelope.version))
            }
            Some(Ok(None)) => ("missing", None),
            _ => ("unavailable", None),
        };
        results.push(json!({"relation":reference.relation,"scope":scope,"path":target,"status":status,"version":version,
            "reference":selected.filter(|_| status == "available").map(|m| format!("/{}/{}",m.path,target))}));
    }
    Ok((json!({"references":results}), 0))
}

pub fn export(memory: &Memory, path: &str, output: &Path) -> Result<(Value, u8), Error> {
    if std::fs::symlink_metadata(output).is_ok() {
        return Err(invalid("export output already exists"));
    }
    let (mount, prefix) = target(memory, path)?;
    let collection = definition(memory, mount, prefix)?;
    let documents = documents(memory, mount, prefix)?;
    let mut records = vec![];
    let mut files = vec![];
    let mut imported_bytes = 0;
    for (path, document) in documents {
        let content = Content::parse(&document.content, prefix)?;
        if content.metadata.collection != prefix {
            return Err(invalid(format!(
                "{path}: collection identity differs; repair it before export"
            )));
        }
        let relative = path
            .strip_prefix(&format!("{prefix}/"))
            .expect("prefix selected")
            .to_string();
        let file = format!("documents/{relative}.md");
        let mut envelope = crate::wire::encode::document(&mount.scope, &path, &document)?;
        envelope
            .as_object_mut()
            .expect("document object")
            .remove("content");
        envelope
            .as_object_mut()
            .expect("document object")
            .remove("redacted");
        let origin: Origin = serde_json::from_value(envelope).expect("encoded origin shape");
        // The exported snapshot must fit the documented ingestion bounds too.
        let mut round_trip = content;
        let source = Source {
            label: "neosian-bundle".into(),
            file: file.clone(),
            sha256: digest(&document.content),
            origin: Some(origin.clone()),
        };
        if !round_trip.metadata.sources.contains(&source) {
            round_trip.metadata.sources.push(source);
        }
        imported_bytes += round_trip.render()?.len();
        if imported_bytes > MAX_TOTAL {
            return Err(invalid(
                "bundle with source provenance exceeds 20 MiB; use a full archive",
            ));
        }
        records.push(Record {
            path: relative,
            file: file.clone(),
            sha256: digest(&document.content),
            origin,
        });
        files.push((file, document.content));
    }
    let bundle = Bundle {
        format: 1,
        collection,
        documents: records,
    };
    let manifest = serde_json::to_string_pretty(&bundle).expect("bundle serializes");
    if manifest.len() > 2 * MAX_BODY {
        return Err(invalid("bundle manifest exceeds 2 MiB"));
    }
    // Create a new private destination. The manifest is the last file published.
    fileio::private_builder().create(output)?;
    for (file, content) in &files {
        fileio::atomic_write(&output.join(file), content, true)?;
    }
    fileio::atomic_write(&output.join("knowledge.json"), &(manifest + "\n"), true)?;
    Ok((
        json!({"success":true,"output":output,"documents":files.len(),"format":1}),
        0,
    ))
}
