//! Profile selection stores references and guards, never copied skill bodies.

use serde_json::{Value, json};

use super::model::{Pin, address, selected};
use crate::knowledge::model::{digest, invalid};
use crate::memory::dispatch::Memory;
use crate::memory::mounts::resolve;
use crate::profile::files;
use crate::store::error::Error;

pub fn pin(memory: &Memory, path: &str) -> Result<Pin, Error> {
    address(path)?;
    let (mount, _) = resolve(&memory.mounts, path)?;
    let profile = memory
        .store
        .profile()
        .ok_or_else(|| invalid("pin requires --profile"))?;
    if !profile.skills.iter().any(|p| p == path) {
        return Err(invalid("select the skill before pinning it"));
    }
    let doc = super::current(memory, path)?;
    super::runtime::check(
        memory.store.as_ref(),
        &memory.mounts,
        path,
        &doc.content,
        doc.envelope.version,
        false,
        &Default::default(),
    )?;
    let source = profile
        .mounts
        .iter()
        .find(|m| m.path == mount.path)
        .ok_or_else(|| invalid("profile mount unavailable"))?;
    Ok(Pin {
        source: source.source.clone(),
        scope: mount.scope.clone(),
        version: doc.envelope.version,
        sha256: digest(&doc.content),
    })
}

pub fn change(
    profile_name: Option<&str>,
    path: &str,
    action: &str,
    write: bool,
    pin: Option<Pin>,
) -> Result<(Value, u8), Error> {
    selected(path)?;
    let name = profile_name
        .ok_or_else(|| invalid("skill selection changes require explicit --profile"))?;
    let home = files::home().map_err(invalid)?;
    let _lock = write
        .then(|| files::lock(&home))
        .transpose()
        .map_err(invalid)?;
    let mut profile = files::read(&home, name).map_err(invalid)?;
    let before = json!({"skills":profile.skills,"skill_pins":profile.skill_pins});
    match action {
        "select" => {
            if !profile.skills.iter().any(|p| p == path) {
                profile.skills.push(path.into());
            }
        }
        "unselect" => {
            profile.skills.retain(|p| p != path);
            profile.skill_pins.remove(path);
        }
        "pin" => {
            profile.skill_pins.insert(
                path.into(),
                pin.ok_or_else(|| invalid("pin requires a current revision"))?,
            );
        }
        "unpin" => {
            profile.skill_pins.remove(path);
        }
        _ => return Err(invalid("unknown skill selection action")),
    }
    crate::profile::encode(&profile).map_err(invalid)?;
    let after = json!({"skills":profile.skills,"skill_pins":profile.skill_pins});
    if write && before != after {
        files::write(&home, name, &profile, true).map_err(invalid)?;
    }
    Ok((
        json!({"success":true,"preview":!write,"profile":name,"operation":action,"before":before,"after":after}),
        0,
    ))
}
