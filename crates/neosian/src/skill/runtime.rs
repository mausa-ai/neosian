//! Resolve current dependencies and text resources through mounted authority only.

use std::collections::BTreeMap;

use serde_json::{Value, json};

use super::model::{MAX_NODES, Revision, address, dependency, parse};
use crate::knowledge::model::{MAX_TOTAL, digest, invalid};
use crate::memory::mounts::{Mount, resolve};
use crate::memory::skill::{shipped_skills, skill_from_document};
use crate::state::State;
use crate::store::error::Error;

pub type Overlay = BTreeMap<String, String>;

struct Check<'a> {
    store: &'a dyn State,
    mounts: &'a [Mount],
    overlay: &'a Overlay,
    selected: bool,
    stack: Vec<String>,
    count: usize,
    bytes: usize,
    dependencies: Vec<Value>,
    resources: Vec<Value>,
}

pub fn provenance(
    store: &dyn State,
    mounts: &[Mount],
    path: &str,
    version: u64,
    text: &str,
) -> Result<Value, Error> {
    let (mount, document) = resolve(mounts, path)?;
    let mut row = json!({"path":path,"scope":mount.scope,"document":document,"version":version,"sha256":digest(text)});
    if let Some(profile) = store.profile() {
        row["workspace_id"] = json!(profile.workspace_id);
        row["source"] = json!(
            profile
                .mounts
                .iter()
                .find(|m| m.path == mount.path)
                .map(|m| &m.source)
        );
    }
    Ok(row)
}

impl Check<'_> {
    fn bytes(&mut self, text: &str) -> Result<(), Error> {
        self.bytes += text.len();
        if self.bytes > MAX_TOTAL {
            return Err(invalid("skill resolution exceeds 20 MiB"));
        }
        Ok(())
    }
    fn read(&self, path: &str) -> Result<(String, u64), Error> {
        let (mount, document) = resolve(self.mounts, path)?;
        if let Some(text) = self.overlay.get(path) {
            return Ok((text.clone(), 0));
        }
        let doc = self
            .store
            .read(&mount.scope, document)?
            .filter(|d| !d.envelope.redacted)
            .ok_or_else(|| invalid(format!("skill document is missing or redacted: {path}")))?;
        Ok((doc.content, doc.envelope.version))
    }
    fn visit(&mut self, path: &str, text: &str, version: u64) -> Result<Value, Error> {
        if self.stack.len() >= 8 || self.count >= MAX_NODES {
            return Err(invalid(
                "skill dependency graph exceeds depth 8 or 64 nodes",
            ));
        }
        if self.stack.iter().any(|p| p == path) {
            return Err(invalid(format!("skill dependency cycle at {path}")));
        }
        if self.selected
            && let Some(profile) = self.store.profile()
        {
            if !profile.skills.iter().any(|p| p == path) {
                return Err(invalid(format!(
                    "dependency is not selected by the profile: {path}"
                )));
            }
            if let Some(pin) = profile.skill_pins.get(path) {
                let (mount, _) = resolve(self.mounts, path)?;
                let source = profile
                    .mounts
                    .iter()
                    .find(|m| m.path == mount.path)
                    .map(|m| m.source.as_str());
                if source != Some(pin.source.as_str())
                    || pin.scope != mount.scope
                    || !(Revision {
                        version: pin.version,
                        sha256: pin.sha256.clone(),
                    })
                    .matches(version, text)
                {
                    return Err(invalid(format!(
                        "skill pin no longer matches current content: {path}"
                    )));
                }
            }
        }
        self.count += 1;
        self.bytes(text)?;
        let (mount, name) = address(path)?;
        let (content, spec) = parse(text)?;
        skill_from_document(name, &content.body, path).map_err(invalid)?;
        self.stack.push(path.into());
        for (file, sha) in &spec.files {
            if self.resources.len() >= 256 {
                return Err(invalid("skill graph exceeds 256 supporting files"));
            }
            let target = format!("{path}/{file}");
            let (raw, revision) = self.read(&target)?;
            self.bytes(&raw)?;
            let body = crate::knowledge::model::Content::parse(&raw, "skills")?.body;
            if digest(&body) != *sha {
                return Err(invalid(format!("supporting file changed: {target}")));
            }
            self.resources.push(provenance(
                self.store,
                self.mounts,
                &target,
                revision,
                &raw,
            )?);
        }
        for required in &spec.requires {
            let target = dependency(mount, &required.skill)?;
            if let Some(builtin) = target.strip_prefix("builtin:") {
                if self.selected
                    && self
                        .store
                        .profile()
                        .is_some_and(|p| !p.skills.contains(&target))
                {
                    return Err(invalid(format!(
                        "dependency is not selected by the profile: {target}"
                    )));
                }
                if !shipped_skills().iter().any(|s| s.name == builtin) {
                    return Err(invalid("built-in dependency is unavailable"));
                }
                self.dependencies.push(json!({"path":target,"source":"builtin","binary_version":env!("CARGO_PKG_VERSION")}));
                continue;
            }
            let (raw, revision) = self.read(&target)?;
            if required
                .revision
                .as_ref()
                .is_some_and(|r| !r.matches(revision, &raw))
            {
                return Err(invalid(format!("dependency revision changed: {target}")));
            }
            let row = self.visit(&target, &raw, revision)?;
            self.dependencies.push(row);
        }
        self.stack.pop();
        let mut row = provenance(self.store, self.mounts, path, version, text)?;
        row["collections"] = json!(
            spec.collections
                .iter()
                .map(|p| format!("/{mount}/{p}"))
                .collect::<Vec<_>>()
        );
        Ok(row)
    }
}

pub fn check(
    store: &dyn State,
    mounts: &[Mount],
    path: &str,
    text: &str,
    version: u64,
    selected: bool,
    overlay: &Overlay,
) -> Result<Value, Error> {
    let mut check = Check {
        store,
        mounts,
        overlay,
        selected,
        stack: vec![],
        count: 0,
        bytes: 0,
        dependencies: vec![],
        resources: vec![],
    };
    let mut row = check.visit(path, text, version)?;
    row["dependencies"] = json!(check.dependencies);
    row["resources"] = json!(check.resources);
    Ok(row)
}
