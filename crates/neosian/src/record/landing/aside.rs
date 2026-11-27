//! What `neosian record held` lists (DESIGN §40.54 item 17.6; ruling
//! "Held selectors" of the T1e2 plan), read from metadata only: the held
//! turns of a spool tree, and its pending turns whose destination this
//! process's environment does not serve (`serving.rs`), which wait for a
//! hook whose environment names it. Each entry comes from the turn's state
//! and reason files and its file's length; no turn's content is read. A
//! reason's message is shown as the lander kept it, cleaned of the
//! client's credential and cut (ruling G3), and cut again here in case the
//! file was changed by hand. A destination is shown by a label that holds
//! no secret: a root, a schema, a URL's host or a profile's name.

use std::fs;
use std::io::{self, Read};
use std::path::{Path, PathBuf};

use serde::Deserialize;

use super::draft::is_opening;
use super::files::{Part, entries};
use super::kept::{self, cut};
use super::queue::Queue;
use super::serving::serving;
use super::state::{Destination, State, public_url};
use crate::conversation::ids::check_conversation_id;

/// A held or pending turn as the listing shows it.
pub struct Entry {
    pub tree: PathBuf,
    pub id: String,
    pub n: u64,
    pub bytes: u64,
    /// The state's `closed_at`; `None` when the state cannot be read.
    pub closed_at: Option<String>,
    /// The destination's label; `None` for a turn held because its policy
    /// changed, or whose state cannot be read.
    pub destination: Option<String>,
    /// A held turn's reason file; `None` when it is missing or unreadable,
    /// and for a pending turn.
    pub said: Option<Said>,
}

impl Entry {
    /// `<conversation id>/<n>`, as the listing names it.
    pub fn name(&self) -> String {
        format!("{}/{}", self.id, self.n)
    }
}

/// A held turn's `<n>.reason` as it was written (`state.rs`).
#[derive(Clone, Debug, Default, Deserialize)]
pub struct Said {
    pub reason: String,
    #[serde(default)]
    pub status: Option<u16>,
    #[serde(default)]
    pub code: Option<String>,
    /// The append was answered by a 2xx that could not be read.
    #[serde(default)]
    pub may_be_stored: bool,
    #[serde(default)]
    pub message: Option<String>,
}

/// The reason file of held turn `n` in `directory`, its message cut again
/// and a code outside the error registry's grammar read as `unknown`, in
/// case the file was written before codes were checked or changed by hand.
pub fn said(directory: &Path, n: u64) -> Option<Said> {
    let text = fs::read(Queue::part(directory, n, "reason")).ok()?;
    let mut said: Said = serde_json::from_slice(&text).ok()?;
    said.message = said.message.map(|message| cut(&message));
    said.code = said.code.map(|code| kept::code(&code, &[]));
    Some(said)
}

/// The opening line `file` begins with, read from its first bytes only;
/// `None` when it has none.
pub fn opening(file: &Path) -> io::Result<Option<String>> {
    let mut head = Vec::with_capacity(512);
    fs::File::open(file)?.take(512).read_to_end(&mut head)?;
    let first = head.split(|byte| *byte == b'\n').next().unwrap_or(&[]);
    let line = std::str::from_utf8(first)
        .ok()
        .filter(|line| is_opening(line));
    Ok(line.map(str::to_string))
}

/// The conversations under `tree/<queue>` whose names the id grammar
/// takes, in name order.
fn conversations(tree: &Path, queue: &str) -> io::Result<Vec<String>> {
    let listing = match fs::read_dir(tree.join(queue)) {
        Ok(listing) => listing,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error),
    };
    let mut names = Vec::new();
    for entry in listing {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned();
        if entry.file_type()?.is_dir() && check_conversation_id(&name).is_ok() {
            names.push(name);
        }
    }
    names.sort();
    Ok(names)
}

/// The turns under `tree/<queue>`, each with its state when it can be
/// read, in conversation and number order. A file gone meanwhile is left
/// out.
pub(super) fn turns(tree: &Path, queue: &str) -> io::Result<Vec<(Entry, Option<State>)>> {
    let mut found = Vec::new();
    for id in conversations(tree, queue)? {
        let directory = tree.join(queue).join(&id);
        for (n, parts) in entries(&directory)? {
            let Some((_, file)) = parts.iter().find(|(part, _)| *part == Part::Span) else {
                continue;
            };
            let bytes = match fs::symlink_metadata(file) {
                Ok(metadata) => metadata.len(),
                Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
                Err(error) => return Err(error),
            };
            let state = State::read(&Queue::part(&directory, n, "state")).ok();
            let entry = Entry {
                tree: tree.to_path_buf(),
                id: id.clone(),
                n,
                bytes,
                closed_at: state.as_ref().map(|state| state.closed_at.clone()),
                destination: state
                    .as_ref()
                    .and_then(|s| s.destination.as_ref().map(label)),
                said: None,
            };
            found.push((entry, state));
        }
    }
    Ok(found)
}

/// The held turns of the spool tree at `tree`.
pub fn held(tree: &Path) -> io::Result<Vec<Entry>> {
    let mut held = Vec::new();
    for (mut entry, _) in turns(tree, "held")? {
        entry.said = said(&tree.join("held").join(&entry.id), entry.n);
        held.push(entry);
    }
    Ok(held)
}

/// The pending turns of the spool tree at `tree` that this process's
/// environment does not serve: their destination is not the one it names,
/// or their state cannot be read or names no destination (the next lander
/// holds such a turn `unreadable`). `home` is the neosian home.
pub fn unserved(tree: &Path, home: &Path) -> io::Result<Vec<Entry>> {
    let mut served: Vec<(Destination, bool)> = Vec::new();
    let mut unserved = Vec::new();
    for (entry, state) in turns(tree, "pending")? {
        let Some((state, destination)) = state
            .as_ref()
            .and_then(|state| Some((state, state.destination.clone()?)))
        else {
            unserved.push(entry);
            continue;
        };
        let known = served.iter().find(|(known, _)| *known == destination);
        let serves = match known {
            Some((_, serves)) => *serves,
            None => {
                let serves = serving(&destination, state, home, None).is_some();
                served.push((destination, serves));
                serves
            }
        };
        if !serves {
            unserved.push(entry);
        }
    }
    Ok(unserved)
}

/// How the listing names a destination: a local root, a PostgreSQL
/// schema, a URL's host and port, or a profile's name; never a digest.
pub fn label(destination: &Destination) -> String {
    match destination {
        Destination::File { root } => root.clone(),
        Destination::Postgres { schema, .. } => format!("PostgreSQL (schema {schema})"),
        Destination::Remote { url, .. } => {
            let shown = public_url(url);
            let rest = shown.split_once("://").map_or(shown.as_str(), |(_, r)| r);
            rest.split('/').next().unwrap_or_default().to_string()
        }
        Destination::Profile { name, .. } => format!("profile {name}"),
    }
}
