//! Two of our servers registered where a client reads (DESIGN §40.53 item 2,
//! kept from §40.51 R3): our entry under the old key and our entry under
//! `neosian` both read for one project, or at user level.

use super::places::{self, Container, Kind};
use crate::clients::{context::Context, naming::NEW};
use serde_json::{Value, json};
use std::path::PathBuf;

/// Where both keys of ours stand for one reading, as file and pointer.
pub struct Pair {
    pub old: String,
    pub new: String,
    pub project: Option<PathBuf>,
    /// neosian cannot change the old entry's file in place, so the fix is
    /// the person's edit, not a command that would fail the same way.
    pub hand: bool,
}

impl Pair {
    pub fn report(&self) -> Value {
        json!({"old": self.old, "new": self.new, "project": self.project, "hand": self.hand})
    }
}

/// Whether the move of `old` (a removal when `new` stands in the same file)
/// is one neosian cannot make in place.
fn by_hand(old: &Container, new: &Container) -> bool {
    let canonical = |c: &Container| c.file.canonicalize().unwrap_or_else(|_| c.file.clone());
    let same = canonical(old) == canonical(new);
    match old.kind {
        Kind::Json(member) => !super::writes::json_editable(old, member, same),
        Kind::Toml => same,
        Kind::Claude(_) => false,
    }
}

fn place(container: &Container, key: &str) -> String {
    format!("{} {}", container.file.display(), container.pointer(key))
}

/// Each reading of a client (user level, and each project with its user
/// level) that holds both keys of ours; `only_here` keeps the user level and
/// the current directory.
pub fn two(client: &'static str, context: &Context, only_here: bool) -> Vec<Pair> {
    let cwd = context
        .cwd
        .canonicalize()
        .unwrap_or_else(|_| context.cwd.clone());
    let mut found: Vec<(Container, bool, bool)> = vec![];
    for container in places::containers(client, context) {
        // A `.jsonc` is read with comments allowed; a file that still
        // cannot be read is not read by the client either, so it holds no
        // server of ours (the move stops on it instead).
        let Ok(entries) = container.entries() else {
            continue;
        };
        let old = entries.old.is_some_and(|e| e.get("url").is_none());
        let new = entries
            .new
            .is_some_and(|e| super::ours(&container, &e, context));
        found.push((container, old, new));
    }
    let mut readings: Vec<Option<PathBuf>> = vec![None];
    for (container, _, _) in &found {
        let project = container
            .project
            .as_ref()
            .map(|p| p.canonicalize().unwrap_or_else(|_| p.clone()));
        if project.is_some()
            && !readings.contains(&project)
            && (!only_here || project.as_ref() == Some(&cwd))
        {
            readings.push(project);
        }
    }
    let mut pairs = vec![];
    for reading in readings {
        let scope: Vec<&(Container, bool, bool)> = found
            .iter()
            .filter(|(c, _, _)| {
                let project = c
                    .project
                    .as_ref()
                    .map(|p| p.canonicalize().unwrap_or_else(|_| p.clone()));
                project.is_none() || project == reading
            })
            .collect();
        let old = scope.iter().find(|(_, o, _)| *o);
        let new = scope.iter().find(|(_, _, n)| *n);
        if let (Some((o, _, _)), Some((n, _, _))) = (old, new) {
            let (old, new) = (place(o, super::super::naming::OLD), place(n, NEW));
            if !pairs.iter().any(|p: &Pair| p.old == old && p.new == new) {
                pairs.push(Pair {
                    old,
                    new,
                    project: reading,
                    hand: by_hand(o, n),
                });
            }
        }
    }
    pairs
}
