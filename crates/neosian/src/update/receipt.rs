//! cargo-dist's receipt must own this executable before its sibling updater may run.

use crate::{
    clients::context::{Context, absolute},
    shape,
};
use serde_json::Value;
use std::path::{Path, PathBuf};

fn locations(context: &Context) -> Vec<PathBuf> {
    if context.env.contains_key("AXOUPDATER_CONFIG_WORKING_DIR") {
        return vec![context.cwd.clone()];
    }
    if let Some(path) = context.env.get("AXOUPDATER_CONFIG_PATH") {
        return vec![path.into()];
    }
    let mut paths = vec![];
    if let Some(path) = context.env.get("XDG_CONFIG_HOME") {
        let path = Path::new(path).join("neosian");
        if path.exists() {
            paths.push(path);
        }
    }
    if cfg!(windows) {
        if let Some(path) = context.env.get("LOCALAPPDATA") {
            paths.push(Path::new(path).join("neosian"));
        }
    } else {
        paths.push(context.home.join(".config/neosian"));
    }
    paths
}

pub fn updater(context: &Context, version: &str) -> Result<PathBuf, &'static str> {
    if shape::kind(&context.env) == "container" {
        return Err("this install is a container");
    }
    let path = locations(context)
        .into_iter()
        .map(|p| p.join("neosian-receipt.json"))
        .find(|p| p.exists())
        .ok_or("no cargo-dist install receipt; reinstall with the original installer")?;
    let receipt: Value = std::fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .ok_or("unreadable cargo-dist install receipt")?;
    let source = &receipt["source"];
    if source["release_type"] != "github"
        || source["owner"] != "mausa-ai"
        || source["name"] != "neosian"
        || source["app_name"] != "neosian"
        || receipt["provider"]["source"] != "cargo-dist"
        || receipt["version"] != version
    {
        return Err("the cargo-dist receipt does not describe this release");
    }
    let executable = absolute(&context.cwd, Path::new(&context.executable));
    let parent = executable
        .parent()
        .ok_or("the executable has no installation directory")?;
    let prefix = receipt["install_prefix"]
        .as_str()
        .map(Path::new)
        .filter(|p| p.is_absolute())
        .ok_or("the cargo-dist receipt has no absolute installation directory")?;
    let prefix = absolute(&context.cwd, prefix);
    let root = if parent.file_name().is_some_and(|p| p == "bin")
        && prefix.file_name().is_none_or(|p| p != "bin")
    {
        parent.parent().unwrap_or(parent)
    } else {
        parent
    };
    let name = executable
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("");
    if root != prefix
        || !receipt["binaries"]
            .as_array()
            .is_some_and(|names| names.iter().any(|n| n.as_str() == Some(name)))
    {
        return Err("the cargo-dist receipt belongs to another installation");
    }
    let updater = parent.join(if cfg!(windows) {
        "neosian-update.exe"
    } else {
        "neosian-update"
    });
    if !shape::executable(&updater) {
        return Err("the sibling neosian-update executable is missing");
    }
    Ok(updater)
}
