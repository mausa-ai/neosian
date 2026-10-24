use super::model::{Request, invalid};
use crate::memory::{
    dispatch::Memory,
    mounts::{self, Mount},
};
use crate::store::error::Error;
use serde_json::{Value, json};

pub fn select<'a>(mounts: &'a [Mount], path: &'a str) -> Result<&'a Mount, Error> {
    let (mount, tail) = mounts::resolve(mounts, path)?;
    if !path.starts_with('/') || !tail.is_empty() {
        return Err(invalid(
            "changes require exactly one mounted scope, such as /project",
        ));
    }
    Ok(mount)
}

pub fn read(memory: &Memory, path: &str, request: &Request) -> Result<Value, Error> {
    request.validate()?;
    let mount = select(&memory.mounts, path)?;
    let page = memory.store.read_changes(&mount.scope, request)?;
    request.check_page(&mount.scope, &page)?;
    let mut result = serde_json::to_value(page).expect("change page serializes");
    for event in result["events"].as_array_mut().expect("events array") {
        if let Some(path) = event["path"].as_str() {
            event["reference"] = json!(format!("/{}/{path}", mount.path));
        }
        if let Some(path) = event["previous_path"].as_str() {
            event["previous_reference"] = json!(format!("/{}/{path}", mount.path));
        }
    }
    result["mount"] = json!(format!("/{}", mount.path));
    if let Some(profile) = memory.store.profile() {
        result["workspace_id"] = json!(profile.workspace_id);
        result["source"] = json!(
            profile
                .mounts
                .iter()
                .find(|m| m.scope == mount.scope)
                .map(|m| &m.source)
        );
    }
    Ok(result)
}

pub fn prune(memory: &Memory, path: &str, through: &str, write: bool) -> Result<Value, Error> {
    let mount = select(&memory.mounts, path)?;
    mounts::writable(mount)?;
    if mount.edit_only {
        return Err(crate::store::error::MemoryError::edit_only(&mount.path).into());
    }
    Ok(
        serde_json::to_value(memory.store.prune_changes(&mount.scope, through, write)?)
            .expect("prune serializes"),
    )
}
