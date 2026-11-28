//! The import's recovery state (the L1c slice of the L1 plan): one
//! owner-only file beside the snapshot, `<home>/login-import/journal.json`.
//! It is written before Begin and again at every step, so a rerun knows the
//! host, the account, the credential and the snapshot the session belongs
//! to, the session itself, what the host left out and how far the import
//! came. It holds no secret: the credential appears as the digest login
//! state already keeps, and the secret stays in `login/credential.toml`.
//! Disposing of the snapshot removes the journal with everything else.

use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::snapshot::DIRECTORY;
use crate::login::host::{self, Result};
use crate::store::fileio;

const FILE: &str = "journal.json";
const FORMAT: u32 = 1;
const LIMIT: u64 = 64 * 1024 * 1024;

/// A unit as the commit lists it.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub(crate) struct Unit {
    pub kind: String,
    pub name: String,
    pub sha256: String,
    pub bytes: u64,
}

/// A unit left out, with its reason.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub(crate) struct Left {
    pub kind: String,
    pub name: String,
    pub reason: String,
}

/// The host's session, as Begin answered it.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub(crate) struct Opened {
    pub id: String,
    pub transfer_url: String,
    pub expires_at: Option<String>,
}

/// How far the import came.
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub(crate) enum Stage {
    /// Begin is next: no session, or one the host ended.
    Begin,
    /// A session is open; units are being sent.
    Transfer,
    /// The commit was sent and its answer is not known.
    Commit,
    /// The host accepted the commit.
    Committed,
    /// The host reported success with its targets; this machine has still
    /// to refresh its assignments and bind its directories (L1d).
    Succeeded,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub(crate) struct Journal {
    pub journal_format: u32,
    /// The discovery URL the person signed in to.
    pub host: String,
    pub import_endpoint: String,
    pub account: String,
    /// The SHA-256 of the machine credential the session belongs to.
    pub credential: String,
    pub snapshot: String,
    pub begun_at: String,
    pub personal: Option<String>,
    pub units: Vec<Unit>,
    /// Left out by this machine before anything was sent (`no_turns`, `unfit`).
    pub local: Vec<Left>,
    pub session: Option<Opened>,
    /// Left out because the host refused them in this session.
    pub omitted: Vec<Left>,
    /// Named by an import that failed; every later session leaves them out.
    pub refused: Vec<Left>,
    pub stage: Stage,
    /// Where each source scope landed, once the host reported success.
    #[serde(default)]
    pub targets: Vec<super::session::Target>,
}

impl Journal {
    /// The units the next commit lists: every unit not left out.
    pub(crate) fn committed(&self) -> Vec<&Unit> {
        let left = |unit: &Unit| {
            self.omitted
                .iter()
                .chain(&self.refused)
                .any(|l| l.kind == unit.kind && l.name == unit.name)
        };
        self.units.iter().filter(|unit| !left(unit)).collect()
    }

    /// What the host was told is left out: its own refusals.
    pub(crate) fn host_omitted(&self) -> Vec<&Left> {
        self.omitted.iter().chain(&self.refused).collect()
    }

    fn validate(&self) -> Result<()> {
        if self.journal_format != FORMAT {
            return Err("unsupported login import journal".into());
        }
        host::endpoint(&self.host, &self.import_endpoint, false)?;
        if let Some(session) = &self.session {
            host::endpoint(&self.host, &session.transfer_url, false)?;
            if !id_ok(&session.id) {
                return Err("invalid login import journal".into());
            }
        }
        Ok(())
    }
}

/// An id a host returned, safe to place in a request path: letters,
/// digits, `-` and `_`, at most 128.
pub(crate) fn id_ok(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 128
        && id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

fn path(home: &Path) -> PathBuf {
    home.join(DIRECTORY).join(FILE)
}

/// The journal, when there is one; refused when anyone but its owner can
/// read or change it, or when it names a destination off the host's origin.
pub(crate) fn read(home: &Path) -> Result<Option<Journal>> {
    let path = path(home);
    let mut file = match fs::File::open(&path) {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("cannot read the login import journal".into()),
    };
    private(&path)?;
    let mut text = String::new();
    file.by_ref()
        .take(LIMIT + 1)
        .read_to_string(&mut text)
        .map_err(|_| "cannot read the login import journal")?;
    if text.len() as u64 > LIMIT {
        return Err("the login import journal is too large".into());
    }
    let journal: Journal =
        serde_json::from_str(&text).map_err(|_| "invalid login import journal")?;
    journal.validate()?;
    Ok(Some(journal))
}

/// Writes the journal through a private temp file and a rename, synced.
pub(crate) fn write(home: &Path, journal: &Journal) -> Result<()> {
    journal.validate()?;
    let text = serde_json::to_string_pretty(journal).map_err(|_| "cannot encode the journal")?;
    fileio::atomic_write(&path(home), &text, true)
        .map_err(|_| "cannot write the login import journal".into())
}

#[cfg(unix)]
fn private(path: &Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    for each in [path, path.parent().unwrap_or(path)] {
        let meta = fs::symlink_metadata(each).map_err(|_| "cannot inspect the import journal")?;
        if meta.file_type().is_symlink() || meta.permissions().mode() & 0o077 != 0 {
            return Err("the login import journal must be private to its owner (0700/0600)".into());
        }
    }
    Ok(())
}

#[cfg(not(unix))]
fn private(_: &Path) -> Result<()> {
    Ok(())
}
