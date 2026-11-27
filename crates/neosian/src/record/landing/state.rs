//! A queued turn's state file, `<n>.state` beside `<n>.jsonl` (DESIGN
//! §40.54 item 17.2, rulings D2, D3 and E3 of the T1e1 plan): plain JSON
//! written with `atomic_write`, read strictly (every key present, none
//! unknown). It holds no content and no secret: a DSN appears only as its
//! SHA-256, a URL without its userinfo and query, and a URL's credential
//! as the SHA-256 of the client's URL and token together (owner O1, taken
//! provisionally).

use std::io;
use std::path::Path;

use serde::{Deserialize, Serialize};

use super::super::fingerprint::digest;
use crate::remote::selection::Selection;
use crate::store::fileio::atomic_write;

/// Where a turn goes, as the environment that closed it named it. A turn
/// is landed only by a process whose own descriptor is the same; a
/// profile turn is matched by its fingerprint instead (17.2).
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase", deny_unknown_fields)]
pub enum Destination {
    File {
        root: String,
    },
    Postgres {
        schema: String,
        dsn_sha256: String,
    },
    Remote {
        url: String,
        credential_sha256: String,
    },
    Profile {
        name: String,
        project: String,
        source: Option<String>,
    },
}

/// The state's keys, in the order they are written. `scope`, `mount`,
/// `provenance`, `project` and `destination` are null only for a span
/// held because its policy changed: they belonged to that policy, and a
/// landing that later meets it again takes them from its selection.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct State {
    pub draft: String,
    pub next_seq: u64,
    #[serde(deserialize_with = "Option::deserialize")]
    pub fingerprint: Option<String>,
    #[serde(deserialize_with = "Option::deserialize")]
    pub destination: Option<Destination>,
    pub attempt: u8,
    pub closed_at: String,
    pub bytes: u64,
    pub agent: String,
    pub actor: String,
    pub session: String,
    pub conversation_id: String,
    #[serde(deserialize_with = "Option::deserialize")]
    pub scope: Option<String>,
    #[serde(deserialize_with = "Option::deserialize")]
    pub mount: Option<String>,
    #[serde(deserialize_with = "Option::deserialize")]
    pub provenance: Option<String>,
    #[serde(deserialize_with = "Option::deserialize")]
    pub parent: Option<String>,
    #[serde(deserialize_with = "Option::deserialize")]
    pub project: Option<String>,
    /// Set and synced before the lander sends the draft's seal, cleared
    /// once a refusal says the seal stored nothing (finding 1 of the T1
    /// review): while it is set, a probe that finds no draft cannot tell a
    /// seal that never arrived from a sealed turn whose receipt an erasure
    /// removed (`seal.rs`). Written only when set, so a state without it
    /// reads as before.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub sealing: bool,
}

/// Why a turn was set aside, written as `<n>.reason`: the reason, for a
/// `refused` turn the status and code the store answered (ruling E10, D3),
/// `"may_be_stored": true` for a turn whose append was answered by a 2xx
/// that could not be read (ruling E14), then the message. Keys in that
/// order.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Reason {
    pub reason: &'static str,
    #[serde(flatten, skip_serializing_if = "Option::is_none")]
    pub answered: Option<Answered>,
    /// The store may hold the turn already: only `record held --retry
    /// --allow-duplicate` puts it back. The marker is typed, so cleaning
    /// the message of a credential never hides it.
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub may_be_stored: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
}

/// A refusal's status and code, both null when the lander refused the
/// turn itself (a part over the bound, a draft longer than the turn).
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Answered {
    pub status: Option<u16>,
    pub code: Option<String>,
}

pub const POLICY_CHANGED: &str = "policy_changed";
pub const UNREADABLE: &str = "unreadable";
/// The store, or the lander itself, refused the turn and it will not be
/// sent again by itself.
pub const REFUSED: &str = "refused";
/// The draft did not match the turn under its id and again under the
/// fresh id (`draft_mismatch`, `draft_size`, 17.4).
pub const MISMATCH: &str = "mismatch";
/// The draft was opened under another actor (`draft_actor`, 17.4).
pub const ACTOR_CHANGED: &str = "actor_changed";

impl Reason {
    pub fn of(reason: &'static str) -> Self {
        Self {
            reason,
            answered: None,
            may_be_stored: false,
            message: None,
        }
    }

    pub fn unreadable(message: String) -> Self {
        Self {
            message: Some(message),
            ..Self::of(UNREADABLE)
        }
    }

    /// A turn whose append a 2xx answered that could not be read: held
    /// `unreadable` with `message`, and marked as maybe stored.
    pub fn may_be_stored(message: String) -> Self {
        Self {
            may_be_stored: true,
            ..Self::unreadable(message)
        }
    }

    pub fn refused(status: Option<u16>, code: Option<String>, message: String) -> Self {
        Self::answered(REFUSED, status, code, message)
    }

    /// A reason that carries the answer's status, code and message:
    /// `refused`, `mismatch` or `actor_changed`.
    pub fn answered(
        reason: &'static str,
        status: Option<u16>,
        code: Option<String>,
        message: String,
    ) -> Self {
        Self {
            answered: Some(Answered { status, code }),
            message: Some(message),
            ..Self::of(reason)
        }
    }

    /// The reason file's text, its message cut to its first 512 scalars
    /// (ruling G3): a lander removes the client's credential first
    /// (`kept.rs`).
    pub fn text(&self) -> String {
        let mut kept = self.clone();
        kept.message = kept.message.map(|message| super::kept::cut(&message));
        let mut text = serde_json::to_string(&kept).expect("a reason serializes");
        text.push('\n');
        text
    }
}

impl State {
    pub fn text(&self) -> String {
        let mut text = serde_json::to_string(self).expect("a state serializes");
        text.push('\n');
        text
    }

    /// Written whole by a rename; `Queue::persist` syncs it with its turn.
    pub fn write(&self, file: &Path) -> io::Result<()> {
        atomic_write(file, &self.text(), false)
    }

    /// Written whole with its bytes and its directory synced, so a power
    /// loss keeps it: the mark a seal is sent under.
    pub fn write_synced(&self, file: &Path) -> io::Result<()> {
        atomic_write(file, &self.text(), true)?;
        super::files::sync_directory(file.parent().unwrap_or(Path::new(".")))
    }

    pub fn read(file: &Path) -> io::Result<Self> {
        let text = std::fs::read_to_string(file)?;
        serde_json::from_str(&text).map_err(|error| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                format!("state {} is unreadable: {error}", file.display()),
            )
        })
    }
}

/// `url` as a descriptor shows it (ruling E3): without userinfo, query or
/// fragment, and without the trailing `/` the client trims.
pub fn public_url(url: &str) -> String {
    let (scheme, rest) = url.split_once("://").unwrap_or(("", url));
    let end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    let (authority, tail) = rest.split_at(end);
    let host = authority
        .rsplit_once('@')
        .map_or(authority, |(_, host)| host);
    let path = &tail[..tail.find(['?', '#']).unwrap_or(tail.len())];
    let shown = match scheme {
        "" => format!("{host}{path}"),
        _ => format!("{scheme}://{host}{path}"),
    };
    shown.trim_end_matches('/').to_string()
}

/// The digest that binds a remote destination: the URL exactly as the
/// client uses it (trailing `/` trimmed, userinfo and query kept) and the
/// token, as one JSON array, so a destination matches only on both.
pub fn credential_digest(url: &str, token: &str) -> String {
    let both = (url.trim_end_matches('/'), token);
    digest(&serde_json::to_vec(&both).expect("strings serialize"))
}

/// A path as a descriptor shows it: its deepest existing ancestor made
/// canonical and the rest appended, so a root shows the same before and
/// after it is created, and through a symlink as its target.
fn shown(path: &Path) -> String {
    let absolute = std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf());
    let mut existing = absolute.as_path();
    let mut rest = Vec::new();
    let resolved = loop {
        if let Ok(real) = std::fs::canonicalize(existing) {
            break rest.iter().rev().fold(real, |full, name| full.join(name));
        }
        match (existing.parent(), existing.file_name()) {
            (Some(parent), Some(name)) => {
                rest.push(name);
                existing = parent;
            }
            _ => break absolute.clone(),
        }
    };
    resolved.to_string_lossy().into_owned()
}

/// The descriptor of `selection` (ruling E3): canonical, and no secret.
pub fn describe(selection: &Selection) -> Destination {
    match selection {
        Selection::File(root) => Destination::File { root: shown(root) },
        Selection::Postgres { dsn, schema } => Destination::Postgres {
            schema: schema.clone(),
            dsn_sha256: digest(dsn.as_bytes()),
        },
        Selection::Remote { url, token } => Destination::Remote {
            url: public_url(url),
            credential_sha256: credential_digest(url, token),
        },
        Selection::Profile(resolved) => Destination::Profile {
            name: resolved.name.clone(),
            project: shown(&resolved.project),
            source: resolved.profile.history.as_ref().map(|h| h.source.clone()),
        },
    }
}
