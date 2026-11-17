//! A repository match carries only a normalized remote digest to the selector.
use super::{
    host::{Machine, Result},
    local,
};
use crate::profile::files::Project;
use std::{
    io::{IsTerminal, Write},
    path::Path,
    process::Command,
};

fn key(remote: &str) -> Option<String> {
    let remote = remote.trim();
    if remote.contains(['?', '#', '\\']) || remote.chars().any(char::is_control) {
        return None;
    }
    let (host, path) = if let Some((scheme, rest)) = remote.split_once("://") {
        if !matches!(scheme, "https" | "ssh" | "git" | "http") {
            return None;
        }
        rest.split_once('/')?
    } else {
        let (host, path) = remote.split_once(':')?;
        if host.contains('/') {
            return None;
        }
        (host, path)
    };
    let host = host.rsplit('@').next()?.to_ascii_lowercase();
    let path = path.trim_matches('/').trim_end_matches(".git");
    if host.is_empty() || path.is_empty() {
        return None;
    }
    Some(format!(
        "sha256:{}",
        local::digest(format!("{host}/{path}").as_bytes())
    ))
}

pub fn select(
    doc: &Machine,
    dir: &Path,
    workspace: Option<&str>,
    prompt: bool,
    create: bool,
    err: &mut impl Write,
) -> Result<Option<Project>> {
    if workspace == Some("none") {
        return Ok(None);
    }
    let dir = dir
        .canonicalize()
        .map_err(|_| "cannot resolve project directory")?;
    if !dir.is_dir() {
        return Err("project must be a directory".into());
    }
    let checkout = dir.ancestors().find(|p| p.join(".git").exists());
    let root = checkout.unwrap_or(&dir);
    let mut chosen = workspace.map(str::to_owned);
    if chosen.is_none() && prompt && std::io::stdin().is_terminal() && checkout.is_some() {
        let key = repository_key(root);
        let mut matches = doc
            .repositories
            .iter()
            .filter(|r| Some(&r.key) == key.as_ref());
        let first = matches
            .next()
            .filter(|_| matches.next().is_none())
            .map(|r| r.profile.as_str());
        writeln!(
            err,
            "Choose an assigned workspace ({}){} or none [{}]:",
            doc.profiles.keys().cloned().collect::<Vec<_>>().join(", "),
            if create { ", new" } else { "" },
            first.unwrap_or("none")
        )
        .and_then(|()| err.flush())
        .map_err(|_| "cannot display workspace choices")?;
        let mut input = String::new();
        std::io::stdin()
            .read_line(&mut input)
            .map_err(|_| "cannot read workspace choice")?;
        chosen = if input.trim().is_empty() {
            first.map(str::to_owned)
        } else {
            Some(input.trim().into())
        };
    }
    let Some(name) = chosen.filter(|s| s != "none") else {
        return Ok(None);
    };
    if !(doc.profiles.contains_key(&name) || create && name == "new") {
        return Err("workspace is not assigned by this host".into());
    }
    Ok(Some(Project {
        root: root.to_path_buf(),
        profile: name,
    }))
}

fn repository_key(root: &Path) -> Option<String> {
    if !root.join(".git").exists() {
        return None;
    }
    let git = Command::new("git")
        .args(["config", "--local", "--get", "remote.origin.url"])
        .current_dir(root)
        .output()
        .ok()
        .filter(|p| p.status.success())?;
    key(std::str::from_utf8(&git.stdout).ok()?)
}

pub fn create(agent: &ureq::Agent, url: &str, bearer: &str, root: &Path) -> Result<String> {
    let name = root
        .file_name()
        .and_then(|s| s.to_str())
        .filter(|s| super::host::display(s, 128))
        .ok_or("project requires a workspace name")?;
    let payload = serde_json::json!({"name":name,"repository_key":repository_key(root)});
    let mut response = agent
        .post(url)
        .header("Authorization", format!("Bearer {bearer}"))
        .send_json(&payload)
        .map_err(super::host::transport)?;
    super::host::status(&response, &[200, 201])?;
    #[derive(serde::Deserialize)]
    struct Created {
        profile: String,
    }
    let created: Created = super::host::body(&mut response)?;
    crate::profile::validate::name(&created.profile)?;
    Ok(created.profile)
}
