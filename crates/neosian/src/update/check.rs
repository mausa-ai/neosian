//! Published binary releases and a separate cache from Python's PyPI check.

use super::version::Version;
use crate::store::{fileio, time::Stamp};
use serde_json::Value;
use std::{io, path::Path, time::Duration};

pub const RELEASE_URL: &str = "https://api.github.com/repos/mausa-ai/neosian/releases/latest";
const STAMP: &str = "binary-update-check";

pub fn latest(url: &str) -> Option<Version> {
    let agent: ureq::Agent = ureq::Agent::config_builder()
        .timeout_global(Some(Duration::from_secs(2)))
        .max_redirects(0)
        .build()
        .into();
    let value: Value = agent
        .get(url)
        .header("Accept", "application/vnd.github+json")
        .header("User-Agent", concat!("neosian/", env!("CARGO_PKG_VERSION")))
        .call()
        .ok()?
        .body_mut()
        .with_config()
        .limit(1024 * 1024)
        .read_json()
        .ok()?;
    release(&value)
}

pub fn release(value: &Value) -> Option<Version> {
    if value["draft"].as_bool()? || value["prerelease"].as_bool()? {
        return None;
    }
    let tag = value["tag_name"].as_str()?;
    let version = Version::parse(tag.strip_prefix('v')?)?;
    if !version.final_release {
        return None;
    }
    let installer = if cfg!(windows) {
        "neosian-installer.ps1"
    } else {
        "neosian-installer.sh"
    };
    value["assets"]
        .as_array()?
        .iter()
        .any(|a| a["name"] == installer)
        .then_some(version)
}

pub fn cached(root: &Path, now: Stamp) -> Option<Version> {
    let text = std::fs::read_to_string(root.join(STAMP)).ok()?;
    let parts: Vec<_> = text.split_whitespace().collect();
    if parts.len() != 2 {
        return None;
    }
    let checked = Stamp::parse(parts[0]).ok()?;
    let age = now.instant() - checked.instant();
    (0..86_400_000_000)
        .contains(&age)
        .then(|| Version::parse(parts[1]))
        .flatten()
}

pub fn stamp(root: &Path, latest: &Version, now: Stamp) -> io::Result<()> {
    fileio::private_mkdir(root)?;
    fileio::atomic_write(&root.join(STAMP), &format!("{} {latest}\n", now.z()), false)
}
