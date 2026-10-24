//! Explicit, bounded filesystem inputs; no archive extraction or foreign transcript guessing.

use std::collections::HashSet;
use std::fs;
use std::io::Read;
use std::path::{Component, Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::model::{
    self, Content, MAX_BODY, MAX_DOCUMENTS, MAX_TOTAL, Origin, Source, digest, invalid,
};
use super::rules::Definition;
use crate::store::error::Error;

pub struct Item {
    pub path: String,
    pub content: Content,
}

pub struct Input {
    pub collection: Option<Definition>,
    pub items: Vec<Item>,
    pub ignored: Vec<String>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Bundle {
    pub format: u32,
    pub collection: Option<Definition>,
    pub documents: Vec<Record>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Record {
    pub path: String,
    pub file: String,
    pub sha256: String,
    pub origin: Origin,
}

pub fn read(path: &Path, limit: usize) -> Result<String, Error> {
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_file() || meta.file_type().is_symlink() {
        return Err(invalid(format!(
            "expected a regular non-symlink file: {}",
            path.display()
        )));
    }
    let mut bytes = vec![];
    fs::File::open(path)?
        .take(limit as u64 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > limit {
        return Err(invalid(format!(
            "input exceeds {limit} bytes: {}",
            path.display()
        )));
    }
    String::from_utf8(bytes).map_err(|_| invalid(format!("input is not UTF-8: {}", path.display())))
}

pub fn contained(root: &Path, relative: &str) -> Result<PathBuf, Error> {
    let path = Path::new(relative);
    if relative.is_empty()
        || relative
            .split('/')
            .any(|part| matches!(part, "" | "." | ".."))
        || relative.contains('\\')
        || path
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
    {
        return Err(invalid(format!(
            "file must be a contained relative path: {relative}"
        )));
    }
    let mut candidate = root.to_path_buf();
    for component in path.components() {
        candidate.push(component);
        if fs::symlink_metadata(&candidate)?.file_type().is_symlink() {
            return Err(invalid(format!(
                "symlink input is not supported: {relative}"
            )));
        }
    }
    Ok(candidate)
}

fn name(path: &Path) -> Result<String, Error> {
    path.components()
        .map(|component| {
            let Component::Normal(component) = component else {
                return Err(invalid("input filenames must be relative"));
            };
            component
                .to_str()
                .filter(|name| !name.contains('\\'))
                .map(str::to_string)
                .ok_or_else(|| {
                    invalid("input filenames must be UTF-8 and cannot contain backslashes")
                })
        })
        .collect::<Result<Vec<_>, _>>()
        .map(|parts| parts.join("/"))
}

fn markdown(path: &Path) -> bool {
    path.extension()
        .and_then(|s| s.to_str())
        .is_some_and(|s| s.eq_ignore_ascii_case("md") || s.eq_ignore_ascii_case("markdown"))
}

fn walk(
    root: &Path,
    relative: &Path,
    files: &mut Vec<PathBuf>,
    ignored: &mut Vec<String>,
    visited: &mut usize,
) -> Result<(), Error> {
    if relative.components().count() > 16 {
        return Err(invalid("input nesting exceeds 16 levels"));
    }
    for entry in fs::read_dir(root.join(relative))? {
        *visited += 1;
        if *visited > 10_000 {
            return Err(invalid("input contains more than 10000 filesystem entries"));
        }
        let entry = entry?;
        let path = relative.join(entry.file_name());
        let kind = entry.file_type()?;
        if kind.is_symlink() {
            return Err(invalid(format!(
                "symlink input is not supported: {}",
                path.display()
            )));
        }
        if kind.is_dir() {
            walk(root, &path, files, ignored, visited)?;
        } else if kind.is_file() && markdown(&path) {
            files.push(path);
        } else {
            ignored.push(name(&path)?);
        }
    }
    Ok(())
}

pub fn load(
    path: &Path,
    format: &str,
    source: Option<&str>,
    collection: &str,
) -> Result<Input, Error> {
    let meta = fs::symlink_metadata(path)?;
    if meta.file_type().is_symlink() {
        return Err(invalid("the input root must not be a symlink"));
    }
    let default_label = path
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("markdown");
    let label = source.unwrap_or(default_label);
    if label.is_empty() || label.chars().count() > 256 {
        return Err(invalid("source label requires 1..256 characters"));
    }
    let mut result = Input {
        collection: None,
        items: vec![],
        ignored: vec![],
    };
    let (root, records): (PathBuf, Vec<Record>) = if format == "bundle" {
        if !meta.is_dir() {
            return Err(invalid("bundle input must be a directory"));
        }
        let bundle: Bundle =
            super::json::parse(&read(&contained(path, "knowledge.json")?, 2 * MAX_BODY)?)?;
        if bundle.format != 1 {
            return Err(invalid("unsupported knowledge bundle format; expected 1"));
        }
        if let Some(definition) = &bundle.collection {
            definition.validate()?;
        }
        result.collection = bundle.collection;
        (path.into(), bundle.documents)
    } else {
        let (root, mut files) = if meta.is_dir() {
            let mut files = vec![];
            walk(path, Path::new(""), &mut files, &mut result.ignored, &mut 0)?;
            (path.to_path_buf(), files)
        } else {
            if !markdown(path) {
                return Err(invalid("Markdown input must end in .md or .markdown"));
            }
            (
                path.parent().unwrap_or(Path::new(".")).to_path_buf(),
                vec![PathBuf::from(
                    path.file_name()
                        .ok_or_else(|| invalid("input filename is missing"))?,
                )],
            )
        };
        files.sort();
        if files.len() > MAX_DOCUMENTS {
            return Err(invalid("import exceeds 1000 documents"));
        }
        let mut total = 0;
        for file in files {
            let logical = name(&file.with_extension(""))?;
            model::ordinary(&logical)?;
            let text = read(&contained(&root, &name(&file)?)?, MAX_BODY)?;
            total += text.len();
            if total > MAX_TOTAL {
                return Err(invalid("import exceeds 20 MiB"));
            }
            let mut content = Content::parse(&text, collection)?;
            content.metadata.collection = collection.into();
            let provenance = Source {
                label: label.into(),
                file: name(&file)?,
                sha256: digest(&text),
                origin: None,
            };
            if !content.metadata.sources.contains(&provenance) {
                content.metadata.sources.push(provenance);
            }
            result.items.push(Item {
                path: logical,
                content,
            });
        }
        validate(&mut result)?;
        return Ok(result);
    };
    if records.len() > MAX_DOCUMENTS {
        return Err(invalid("import exceeds 1000 documents"));
    }
    let mut total = 0;
    for record in records {
        model::ordinary(&record.path)?;
        record.origin.validate()?;
        let text = read(&contained(&root, &record.file)?, MAX_BODY)?;
        total += text.len();
        if total > MAX_TOTAL {
            return Err(invalid("import exceeds 20 MiB"));
        }
        if digest(&text) != record.sha256 {
            return Err(invalid(format!("checksum mismatch: {}", record.file)));
        }
        let mut content = Content::parse(&text, collection)?;
        content.metadata.collection = collection.into();
        let provenance = Source {
            label: source.unwrap_or("neosian-bundle").into(),
            file: record.file,
            sha256: record.sha256,
            origin: Some(record.origin),
        };
        if !content.metadata.sources.contains(&provenance) {
            content.metadata.sources.push(provenance);
        }
        result.items.push(Item {
            path: record.path,
            content,
        });
    }
    validate(&mut result)?;
    Ok(result)
}

fn validate(input: &mut Input) -> Result<(), Error> {
    let mut names = HashSet::new();
    let mut total = 0;
    for item in &input.items {
        if !names.insert(item.path.to_ascii_lowercase()) {
            return Err(invalid(format!(
                "duplicate or case-colliding path: {}",
                item.path
            )));
        }
        total += item.content.render()?.len();
    }
    if total > MAX_TOTAL {
        return Err(invalid("import with metadata exceeds 20 MiB"));
    }
    input.items.sort_by(|a, b| a.path.cmp(&b.path));
    input.ignored.sort();
    Ok(())
}
