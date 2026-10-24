//! Explicit portable text packages. No file is installed or executed by loading a skill.

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

use serde_json::{Value, json};

use super::model::{MAX_FILES, Spec, address, parse};
use super::runtime::{Overlay, check};
use crate::knowledge::input::{Input, Item, contained, read};
use crate::knowledge::model::{
    Content, MAX_BODY, MAX_TOTAL, Origin, Source, digest, invalid, ordinary,
};
use crate::memory::dispatch::Memory;
use crate::memory::mounts::resolve;
use crate::store::error::Error;

fn files(
    root: &Path,
    dir: &str,
    found: &mut Vec<String>,
    visited: &mut usize,
) -> Result<(), Error> {
    if dir.split('/').count() > 16 {
        return Err(invalid("skill directory exceeds 16 levels"));
    }
    for entry in fs::read_dir(root.join(dir))? {
        *visited += 1;
        if *visited > 4096 {
            return Err(invalid("skill directory exceeds 4096 entries"));
        }
        let entry = entry?;
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| invalid("skill filename is not UTF-8"))?;
        if name.contains('\\') {
            return Err(invalid("backslashes are not portable filenames"));
        }
        let path = if dir.is_empty() {
            name
        } else {
            format!("{dir}/{name}")
        };
        ordinary(&format!("skills/resources/{path}"))?;
        let kind = entry.file_type()?;
        if kind.is_dir() {
            files(root, &path, found, visited)?;
        } else if kind.is_file() {
            found.push(path);
        } else {
            return Err(invalid(
                "skill packages require regular files without symlinks",
            ));
        }
        if found.len() > MAX_FILES + 2 {
            return Err(invalid("skill package exceeds 64 supporting files"));
        }
    }
    Ok(())
}

fn sourced(raw: &str, file: &str, label: &str) -> Result<Content, Error> {
    let mut content = Content::parse(raw, "skills")?;
    content.metadata.collection = "skills".into();
    let source = Source {
        label: label.into(),
        file: file.into(),
        sha256: digest(raw),
        origin: None,
    };
    if !content.metadata.sources.contains(&source) {
        content.metadata.sources.push(source);
    }
    Ok(content)
}

pub fn load(input: &Path, to: &str) -> Result<Input, Error> {
    let (_, name) = address(to)?;
    let meta = fs::symlink_metadata(input)?;
    if meta.file_type().is_symlink() {
        return Err(invalid("skill input cannot be a symlink"));
    }
    let label = input
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("skill");
    let (root, main, mut names) = if meta.is_dir() {
        let mut names = vec![];
        files(input, "", &mut names, &mut 0)?;
        names.sort();
        (input, "SKILL.md", names)
    } else {
        if !input
            .extension()
            .and_then(|s| s.to_str())
            .is_some_and(|s| s.eq_ignore_ascii_case("md"))
        {
            return Err(invalid(
                "skill input must be Markdown or a package directory",
            ));
        }
        (input.parent().unwrap_or(Path::new(".")), label, vec![])
    };
    let raw = read(&contained(root, main)?, MAX_BODY)?;
    let mut content = sourced(&raw, main, label)?;
    let (_, existing) = parse(&raw)?;
    let controlled = names.iter().any(|p| p == "skill.json")
        || content.metadata.attributes.contains_key("skill");
    let mut spec: Spec = if names.iter().any(|p| p == "skill.json") {
        crate::knowledge::json::parse(&read(&contained(root, "skill.json")?, 64 * 1024)?)?
    } else {
        existing
    };
    spec.validate()?;
    names.retain(|s| s != main && s != "skill.json");
    if controlled
        && names
            .iter()
            .cloned()
            .collect::<std::collections::BTreeSet<_>>()
            != spec.files.keys().cloned().collect()
    {
        return Err(invalid(
            "supporting files must exactly match the skill manifest",
        ));
    }
    let mut items = vec![];
    let mut total = 0;
    for file in names {
        let raw = read(&contained(root, &file)?, MAX_BODY)?;
        let supporting = sourced(&raw, &file, label)?;
        let sha = digest(&supporting.body);
        if controlled && spec.files.get(&file) != Some(&sha) {
            return Err(invalid(format!("supporting checksum mismatch: {file}")));
        }
        spec.files.insert(file.clone(), sha);
        total += supporting.render()?.len();
        items.push(Item {
            path: format!("{name}/{file}"),
            content: supporting,
        });
    }
    spec.validate()?;
    content
        .metadata
        .attributes
        .insert("skill".into(), json!(spec));
    crate::memory::skill::skill_from_document(name, &content.body, to).map_err(invalid)?;
    total += content.render()?.len();
    if total > MAX_TOTAL {
        return Err(invalid("skill package exceeds 20 MiB"));
    }
    // Write the root last. A partial supporting-file update makes old hashes fail closed.
    items.push(Item {
        path: name.into(),
        content,
    });
    Ok(Input {
        collection: None,
        items,
        ignored: vec![],
    })
}

pub fn overlay(input: &Input, mount: &str) -> Result<Overlay, Error> {
    input
        .items
        .iter()
        .map(|item| {
            Ok((
                format!("/{mount}/skills/{}", item.path),
                item.content.render()?,
            ))
        })
        .collect()
}

pub fn import(
    memory: &Memory,
    path: &str,
    input: Input,
    replace: bool,
    write: bool,
) -> Result<(Value, u8), Error> {
    let (mount, _) = address(path)?;
    let overlay = overlay(&input, mount)?;
    let text = overlay
        .get(path)
        .ok_or_else(|| invalid("skill package has no root"))?;
    check(
        memory.store.as_ref(),
        &memory.mounts,
        path,
        text,
        0,
        false,
        &overlay,
    )?;
    crate::knowledge::plan::import(
        memory,
        &format!("/{mount}/skills"),
        input,
        if replace { "replace" } else { "fail" },
        write,
    )
}

fn with_origin(memory: &Memory, path: &str) -> Result<String, Error> {
    let (mount, key) = resolve(&memory.mounts, path)?;
    let doc = super::current(memory, path)?;
    let envelope = crate::wire::encode::document(&mount.scope, key, &doc)?;
    let origin: Origin = serde_json::from_value(json!({
        "scope":mount.scope,"path":key,"version":doc.envelope.version,
        "created_at":envelope["created_at"],"updated_at":envelope["updated_at"],
        "actor":envelope["actor"],"extra":envelope["extra"]
    }))
    .map_err(|_| invalid("unsupported source envelope metadata"))?;
    origin.validate()?;
    let mut content = Content::parse(&doc.content, "skills")?;
    let source = Source {
        label: "neosian-skill".into(),
        file: key.into(),
        sha256: digest(&doc.content),
        origin: Some(origin),
    };
    if !content.metadata.sources.contains(&source) {
        content.metadata.sources.push(source);
    }
    content.render()
}

pub fn export(memory: &Memory, path: &str, output: &Path) -> Result<(Value, u8), Error> {
    address(path)?;
    let doc = super::current(memory, path)?;
    check(
        memory.store.as_ref(),
        &memory.mounts,
        path,
        &doc.content,
        doc.envelope.version,
        false,
        &Overlay::new(),
    )?;
    let (_, spec) = parse(&doc.content)?;
    let mut texts = BTreeMap::from([("SKILL.md".to_string(), with_origin(memory, path)?)]);
    for file in spec.files.keys() {
        texts.insert(
            file.clone(),
            with_origin(memory, &format!("{path}/{file}"))?,
        );
    }
    if texts.values().map(String::len).sum::<usize>() > MAX_TOTAL {
        return Err(invalid("export provenance exceeds 20 MiB"));
    }
    crate::store::fileio::private_builder().create(output)?;
    for (file, text) in texts {
        let target = output.join(file);
        crate::store::fileio::private_mkdir(target.parent().expect("package directory"))?;
        crate::store::fileio::atomic_write(&target, &text, true)?;
    }
    // The manifest is the completion marker; never overwrite an existing output directory.
    crate::store::fileio::atomic_write(
        &output.join("skill.json"),
        &serde_json::to_string_pretty(&spec).expect("skill spec serializes"),
        true,
    )?;
    Ok((
        json!({"success":true,"path":path,"output":output,"files":spec.files.len()}),
        0,
    ))
}
