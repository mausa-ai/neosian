//! Current collection checks, permission-bounded references and portable bundles.

use std::path::Path;

use serde_json::{Value, json};

use super::governance::{self, Policy};
use super::input::{Bundle, Record};
use super::model::{Content, DEFINITION, MAX_BODY, Origin, Source, digest, invalid, ordinary};
use super::rules::Issue;
use super::{definition, target};
use crate::memory::dispatch::Memory;
use crate::memory::mounts::Mount;
use crate::store::{Document, error::Error};

fn documents<'a>(
    memory: &'a Memory,
    mount: &'a Mount,
    prefix: &'a str,
) -> Result<impl Iterator<Item = Result<(String, Document), Error>> + 'a, Error> {
    let limits = crate::limits::Knowledge::load()?;
    let entries = crate::state::scan::Entries::new(
        memory.store.as_ref(),
        &mount.scope,
        &format!("{prefix}/"),
    );
    let mut count = 0;
    let mut total = 0;
    Ok(entries.filter_map(move |entry| {
        (|| {
            let entry = entry?;
            if entry.path == format!("{prefix}/{DEFINITION}") || entry.redacted {
                return Ok(None);
            }
            if entry.path.ends_with(&format!("/{DEFINITION}")) {
                return Err(invalid(
                    "nested collections must be checked or exported separately",
                ));
            }
            ordinary(&entry.path)?;
            let Some(document) = memory.store.read(&mount.scope, &entry.path)? else {
                return Err(invalid(
                    "collection changed during reading; retry with paused writers",
                ));
            };
            if document.envelope.redacted {
                return Ok(None);
            }
            count += 1;
            total += document.content.len();
            limits.check(count, total)?;
            if document.content.len() > MAX_BODY {
                return Err(invalid("document exceeds 1 MiB"));
            }
            Ok(Some((entry.path, document)))
        })()
        .transpose()
    }))
}

pub fn validate(memory: &Memory, path: &str) -> Result<(Value, u8), Error> {
    let (mount, prefix) = target(memory, path)?;
    let definition = definition(memory, mount, prefix)?;
    let documents = documents(memory, mount, prefix)?;
    let mut issues = vec![];
    let mut checked = 0;
    for document in documents {
        let (path, document) = document?;
        checked += 1;
        match Content::parse(&document.content, prefix) {
            Ok(content) => {
                if let Err(error) = governance::record(&content) {
                    issues.push(Issue {
                        path: path.clone(),
                        field: format!("attributes.{}", governance::KEY),
                        message: super::text_error(error),
                    });
                }
                if content.metadata.collection != prefix {
                    issues.push(Issue {
                        path: path.clone(),
                        field: "collection".into(),
                        message: "document names a different collection".into(),
                    });
                }
                if let Some(definition) = &definition {
                    issues.extend(definition.check(&path, &content.metadata.attributes));
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
        json!({"success":code == 0,"path":path,"checked":checked,"schema_enabled":definition.is_some_and(|d| d.schema.is_some()),"issues":issues}),
        code,
    ))
}

pub fn references(memory: &Memory, path: &str, policy: Policy) -> Result<(Value, u8), Error> {
    let (mount, path) = target(memory, path)?;
    let found = memory
        .store
        .read(&mount.scope, path)?
        .filter(|d| !d.envelope.redacted)
        .ok_or_else(|| invalid("reference source is absent or redacted"))?;
    let fallback = path
        .rsplit_once('/')
        .map_or("knowledge", |(parent, _)| parent);
    let mut content = Content::parse(&found.content, fallback)?;
    let now = crate::store::time::Stamp::now();
    let policy = policy.selected(memory.store.as_ref(), &mount.scope);
    if !governance::eligible(policy, &mount.scope, path, &found.content, now) {
        return Ok((
            json!({"references":[],"status":"excluded","policy":policy}),
            0,
        ));
    }
    if let Ok(Some(record)) = governance::record(&content)
        && let Some(link) = record.superseded_by
    {
        content.metadata.references.push(super::model::Reference {
            relation: "superseded_by".into(),
            scope: Some(link.scope),
            path: link.path,
        });
    }
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
            Some(Ok(Some(doc)))
                if !doc.envelope.redacted
                    && governance::eligible(
                        policy.selected(memory.store.as_ref(), &scope),
                        &scope,
                        &target,
                        &doc.content,
                        now,
                    ) =>
            {
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
    let limits = crate::limits::Knowledge::load()?;
    let parent = output
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let staging = crate::limits::Scratch::beside(parent)?;
    let mut manifest_bytes = 0;
    let mut imported_bytes = 0;
    for document in documents {
        let (path, document) = document?;
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
        limits.check(records.len() + 1, imported_bytes)?;
        let record = Record {
            path: relative,
            file: file.clone(),
            sha256: digest(&document.content),
            origin,
        };
        manifest_bytes += serde_json::to_vec(&record)
            .expect("record serializes")
            .len();
        if manifest_bytes > limits.manifest() {
            return Err(invalid(
                "bundle manifest budget exceeded; export a smaller collection",
            ));
        }
        records.push(record);
        staging.put(Path::new(&file), &document.content)?;
    }
    let count = records.len();
    let bundle = Bundle {
        format: 1,
        collection,
        documents: records,
    };
    let manifest = serde_json::to_string_pretty(&bundle).expect("bundle serializes");
    if manifest.len() > limits.manifest() {
        return Err(invalid(
            "bundle manifest budget exceeded; export a smaller collection",
        ));
    }
    staging.put(Path::new("knowledge.json"), &(manifest + "\n"))?;
    // Only a complete staged bundle is published. A failed rename leaves no
    // manifest at the requested output and the private stage is cleaned up.
    if std::fs::symlink_metadata(output).is_ok() {
        return Err(invalid("export output already exists"));
    }
    std::fs::rename(&staging.directory, output)?;
    Ok((
        json!({"success":true,"output":output,"documents":count,"format":1}),
        0,
    ))
}
