//! Portable workspace configuration, independent of storage records and credentials.

pub mod cli;
pub mod context;
pub(crate) mod files;
pub mod filter;
pub mod model;
pub mod resolve;
mod routing;
mod validate;

pub use model::Profile;

pub fn parse(text: &str) -> Result<Profile, String> {
    if text.len() > 262_144 {
        return Err("profile exceeds 256 KiB".into());
    }
    // TOML diagnostics can include source lines containing an accidentally pasted secret.
    let profile: Profile = toml::from_str(text)
        .map_err(|_| "invalid profile TOML or unknown/mistyped field".to_string())?;
    validate::profile(&profile)?;
    Ok(profile)
}

pub fn encode(profile: &Profile) -> Result<String, String> {
    validate::profile(profile)?;
    toml::to_string_pretty(profile).map_err(|_| "cannot serialize profile".into())
}
