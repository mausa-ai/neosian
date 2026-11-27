//! Private machine identity. Credentials never enter a portable profile,
//! profile binding, command line, status document or recovery journal.
use super::{
    device::{Credential, token_ok},
    host::{self, Account, Device, Result},
};
pub use crate::knowledge::model::digest;
use crate::store::{fileio, time::Stamp};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};

pub const TOKEN_ENV: &str = "NEOSIAN_LOGIN_TOKEN";
pub const STATE: &str = "state.toml";
pub const CREDENTIAL: &str = "credential.toml";
pub const STAGED: &str = "credential.next.toml";
pub const JOURNAL: &str = "pending.toml";

#[derive(Clone, Copy, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Source {
    Environment,
    File,
}

#[derive(Clone, Deserialize, Serialize)]
pub struct State {
    pub login_format: u32,
    pub host: String,
    pub discovery: host::Discovery,
    pub account: Account,
    pub device: Option<Device>,
    pub state_url: String,
    pub default_profile: String,
    pub managed: BTreeSet<String>,
    pub installed: BTreeMap<String, String>,
    pub projects: Vec<crate::profile::files::Project>,
    pub capture: bool,
    pub notice: String,
    pub etag: Option<String>,
    /// The `Neosian-Capabilities` value sent for the installed document.
    #[serde(default)]
    pub capabilities: Option<String>,
    pub refreshed_at: String,
    pub refresh_after_seconds: u64,
    pub credential_source: Source,
    pub credential_digest: String,
    pub signed_out: bool,
}

fn check(path: &Path, directory: bool) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(meta) => {
            if (directory && !meta.is_dir()) || (!directory && !meta.is_file()) {
                return Err("unexpected file type in private login configuration".into());
            }
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                if meta.file_attributes() & 0x400 != 0 {
                    return Err("private login paths cannot be Windows reparse points".into());
                }
                super::windows_acl::protected(path)?;
            }
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                if meta.permissions().mode() & 0o077 != 0 {
                    return Err("login directory and files must be accessible only to their owner (0700/0600)".into());
                }
            }
            Ok(())
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err("cannot inspect private login configuration".into()),
    }
}
pub(super) fn path(home: &Path, name: &str) -> Result<PathBuf> {
    let dir = home.join("login");
    if dir.exists() {
        #[cfg(windows)]
        super::windows_acl::protected(home)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let metadata = fs::metadata(home).map_err(|_| "cannot inspect managed login home")?;
            if metadata.permissions().mode() & 0o022 != 0 {
                return Err("managed login requires a home writable only by its owner".into());
            }
        }
    }
    check(&dir, true)?;
    let file = dir.join(name);
    check(&file, false)?;
    Ok(file)
}
pub fn read<T: DeserializeOwned>(home: &Path, name: &str) -> Result<Option<T>> {
    use std::io::Read;
    let file = match fs::File::open(path(home, name)?) {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("cannot read private login configuration".into()),
    };
    let mut buf = String::new();
    let limit = 16 * 1024 * 1024;
    file.take(limit + 1)
        .read_to_string(&mut buf)
        .map_err(|_| "cannot read private login configuration")?;
    if buf.len() as u64 > limit {
        return Err("private login configuration too large".into());
    }
    toml::from_str(&buf)
        .map(Some)
        .map_err(|_| "invalid private login configuration".into())
}
pub fn write(home: &Path, name: &str, data: &impl Serialize) -> Result<()> {
    let file = path(home, name)?;
    let text = toml::to_string(data).map_err(|_| "cannot encode private login configuration")?;
    if text.len() > 16 * 1024 * 1024 {
        return Err("private login configuration too large".into());
    }
    fileio::atomic_write(&file, &text, true)
        .map_err(|_| "cannot write private login configuration".into())
}
pub fn remove(home: &Path, name: &str) -> Result<()> {
    match fs::remove_file(path(home, name)?) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err("cannot remove private login configuration".into()),
    }
}
pub fn lock(home: &Path) -> Result<fs::File> {
    crate::store::lock::acquire(&path(home, "operation.lock")?)
        .map_err(|_| "cannot lock login configuration".into())
}

const OFF: &str = "capture-off.toml";

#[derive(Serialize, Deserialize, PartialEq)]
pub struct CaptureOff {
    revision: String,
}

pub fn capture_off(home: &Path) -> Result<bool> {
    Ok(path(home, OFF)?.exists())
}

pub fn disable_capture(home: &Path) -> Result<()> {
    let _lock = crate::store::lock::acquire(&path(home, "capture.lock")?)
        .map_err(|_| "cannot lock local recording preference")?;
    write(
        home,
        OFF,
        &CaptureOff {
            revision: uuid::Uuid::new_v4().to_string(),
        },
    )
}

pub fn off_revision(home: &Path) -> Result<Option<CaptureOff>> {
    read(home, OFF)
}

/// A foreground login can re-enable only the preference it observed at start.
/// A later request to turn recording off wins even during device approval.
pub fn enable_capture(home: &Path, observed: &Option<CaptureOff>) -> Result<()> {
    let _lock = crate::store::lock::acquire(&path(home, "capture.lock")?)
        .map_err(|_| "cannot lock local recording preference")?;
    if &off_revision(home)? == observed {
        remove(home, OFF)?;
    }
    Ok(())
}

pub fn try_lock(home: &Path) -> Result<fs::File> {
    let file = fileio::private()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(path(home, "operation.lock")?)
        .map_err(|_| "cannot open login lock")?;
    file.try_lock()
        .map_err(|_| "login update already running")?;
    Ok(file)
}

pub fn state(home: &Path) -> Result<Option<State>> {
    if path(home, JOURNAL)?.exists() {
        return Err("interrupted login update; run neosian login --refresh to recover".into());
    }
    let state: Option<State> = read(home, STATE)?;
    if let Some(s) = &state {
        if s.login_format != 1 || !s.managed.contains(&s.default_profile) {
            return Err("unsupported or invalid login state".into());
        }
        host::endpoint(&s.host, &s.state_url, false)?;
        s.discovery.validate(&s.host)?;
    }
    Ok(state)
}

#[cfg(windows)]
pub use super::windows::{read_secret, write_secret};

#[cfg(not(windows))]
pub fn read_secret(home: &Path, file: &str) -> Result<Option<Credential>> {
    read(home, file)
}

#[cfg(not(windows))]
pub fn write_secret(home: &Path, file: &str, cred: &Credential) -> Result<()> {
    write(home, file, cred)
}

pub fn require_manual(home: &Path, name: &str) -> Result<()> {
    if state(home)?.is_some_and(|s| s.managed.contains(name)) {
        return Err(
            "host-managed profiles and source bindings are changed by neosian login".into(),
        );
    }
    Ok(())
}

pub fn environment() -> Option<String> {
    std::env::var(TOKEN_ENV).ok().filter(|s| !s.is_empty())
}

/// Only the verified routing identity is retained by long-running MCP bridges.
/// Every HTTP operation obtains the current validated token from this home.
pub struct MachineCredential {
    pub home: PathBuf,
    pub host: String,
    pub account: String,
    pub state_url: String,
    pub profile: String,
    pub workspace: String,
}
impl MachineCredential {
    pub fn token(&self) -> Result<String> {
        let s = state(&self.home)?.ok_or("machine disconnected; run neosian login")?;
        if s.host != self.host
            || s.account.id != self.account
            || s.state_url != self.state_url
            || !s.managed.contains(&self.profile)
            || crate::profile::files::read(&self.home, &self.profile)?.workspace_id
                != self.workspace
        {
            return Err("machine assignment changed; restart the client".into());
        }
        s.token(&self.home)
    }
}

impl State {
    pub fn token(&self, home: &Path) -> Result<String> {
        let refused = "not logged in with the assigned credential; run neosian login";
        if self.signed_out {
            return Err(refused.into());
        }
        if self.credential_source == Source::Environment {
            return environment()
                .filter(|s| token_ok(s) && digest(s.as_bytes()) == self.credential_digest)
                .ok_or_else(|| refused.into());
        }
        let cred = self.stored(home)?;
        if cred.expires_at.as_ref().is_some_and(|date| {
            Stamp::parse(date).map_or(true, |t| t.instant() <= Stamp::now().instant())
        }) || environment().is_some_and(|value| value != cred.token)
        {
            return Err(refused.into());
        }
        Ok(cred.token)
    }

    pub fn stored(&self, home: &Path) -> Result<Credential> {
        let refused = "stored credential differs from the verified machine login";
        let cred = read_secret(home, CREDENTIAL)?.ok_or(refused)?;
        if cred.credential_format != 1
            || cred.host != self.host
            || cred.account_id != self.account.id
            || !token_ok(&cred.token)
            || digest(cred.token.as_bytes()) != self.credential_digest
        {
            return Err(refused.into());
        }
        Ok(cred)
    }

    pub fn due(&self) -> bool {
        Stamp::parse(&self.refreshed_at).map_or(true, |time| {
            Stamp::now().instant().saturating_sub(time.instant()) / 1_000_000
                >= self.refresh_after_seconds.clamp(1, 86400) as i64
        })
    }
    pub fn verify_profile(
        &self,
        name: &str,
        profile: &crate::profile::Profile,
        bindings: &BTreeMap<String, crate::profile::files::Binding>,
    ) -> Result<()> {
        let bytes = crate::profile::encode(profile)?;
        if self.installed.get(name)
            != Some(&super::apply::signature(bytes.as_bytes(), Some(bindings))?)
        {
            return Err(format!(
                "managed profile {name} differs from its installed assignment"
            ));
        }
        Ok(())
    }

    /// A long-running client may retain its selected profile past a logout.
    /// Never open an old selection with another host's or account's credential.
    pub fn bound(&self, home: &Path, profile: &str) -> Result<String> {
        let current = state(home)?.ok_or("managed profile has been disconnected")?;
        if current.host != self.host
            || current.account.id != self.account.id
            || current.state_url != self.state_url
            || !current.managed.contains(profile)
        {
            return Err("machine assignment changed; restart the client".into());
        }
        current.token(home)
    }
}

pub fn status(home: &Path, s: &State) -> Result<serde_json::Value> {
    let cred: Option<Credential> = if s.credential_source == Source::Environment {
        None
    } else {
        read_secret(home, CREDENTIAL)?
    };
    let expiry = cred.as_ref().and_then(|c| c.expires_at.as_ref());
    let soon = expiry
        .and_then(|s| Stamp::parse(s).ok())
        .is_some_and(|t| t.instant() - Stamp::now().instant() < 14 * 86400 * 1_000_000);
    Ok(
        serde_json::json!({"host":s.host,"name":s.discovery.name,"account":s.account,"state_url":s.state_url,
        "default_profile":s.default_profile,"managed":s.managed,"refreshed_at":s.refreshed_at,
        "device":s.device, "credential_source":s.credential_source,
        "connected":s.token(home).is_ok(),"token_name":cred.as_ref().map(|c|&c.name),
        "expires_at":expiry.cloned(),"expires_soon":soon}),
    )
}
