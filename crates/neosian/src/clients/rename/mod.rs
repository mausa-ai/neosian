//! The rename as a reinstall of what neosian installed (DESIGN §40.53): per
//! client, our entry under `neosian-memory` is removed and the same value
//! written under `neosian`, in every scope the client reads, whole client or
//! nothing. `update` does it by itself only when no other server is named
//! `neosian` and the mention search finds nothing a person wrote; the person
//! runs `neosian setup --refresh --rename` otherwise. Nothing a person wrote
//! is edited.

pub mod backup;
mod claude_cli;
pub mod jsonedit;
pub mod places;
pub mod read;
pub mod report;
mod search;
mod search_files;
pub mod servers;
pub mod text;
mod toml_key;
pub mod undo;
mod writes;

pub mod moves;

pub use moves::run;

/// The hint a failed or two-server client carries.
pub fn claude_cli_hint() -> &'static str {
    claude_cli::AFTER_FAILURE
}
pub use search::Mention;

use crate::clients::{
    context::Context,
    naming::{self, NEW, OLD},
    receipt::{self, Book},
    targets,
};
use places::Container;
use serde_json::Value;
use std::path::PathBuf;

/// The client name with the static lifetime the containers carry.
pub fn static_client(client: &str) -> &'static str {
    naming::CLIENTS
        .into_iter()
        .find(|c| *c == client)
        .expect("a client the rename knows")
}

/// One container's step: our old entry moved, or removed beside an equal
/// `neosian` of ours.
#[derive(Clone)]
pub struct Step {
    pub container: Container,
    pub entry: Value,
    pub remove_only: bool,
}

/// What stops a client from moving.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Stop {
    /// Another server named `neosian` (file, pointer): refused both ways.
    Taken(PathBuf, String),
    /// Both keys ours and different (file, pointer).
    Differ(PathBuf, String),
    /// A file that holds registrations cannot be read.
    Unreadable(PathBuf),
    /// The receipt cannot be read.
    Receipt(PathBuf),
    /// Another installed client reads a shared file and does not move now.
    Shared(PathBuf, &'static str),
    /// A file the client reads names a key and neosian cannot read or edit
    /// it (comments, a duplicate key): the person renames the key there.
    Unedited(PathBuf),
    /// A file to change is not writable (or not valid UTF-8): left alone.
    Unwritable(PathBuf, &'static str),
    /// Put back with `setup --undo-rename`: moved again only by the command.
    Held,
}

/// A client's plan: its steps, what stops it, what the search found, and the
/// hosted entries under the old key that are listed and never moved.
pub struct Plan {
    pub client: &'static str,
    pub label: &'static str,
    pub steps: Vec<Step>,
    pub stops: Vec<Stop>,
    pub found: search::Found,
    pub hosted: Vec<(PathBuf, String)>,
}

impl Plan {
    /// Whether anything of ours stands, or may stand, under the old key.
    pub fn wanted(&self) -> bool {
        !self.steps.is_empty()
            || self
                .stops
                .iter()
                .any(|s| matches!(s, Stop::Unedited(_) | Stop::Unreadable(_)))
    }

    /// Whether `update` moves it by itself (§40.53 item 2).
    pub fn by_itself(&self) -> bool {
        self.wanted() && self.stops.is_empty() && !self.found.blocks()
    }

    /// Whether the person's command moves it: everything but a collision,
    /// differing entries, an unreadable registration file or receipt, or a
    /// shared file another client keeps.
    pub fn by_command(&self) -> bool {
        self.wanted() && self.stops.iter().all(|s| *s == Stop::Held)
    }
}

/// Whether two entries of ours are the same: every member of the old one in
/// the new one, which may add `type: "stdio"`.
fn equal(old: &Value, new: &Value) -> bool {
    let (Some(old), Some(new)) = (old.as_object(), new.as_object()) else {
        return false;
    };
    old.iter().all(|(k, v)| new.get(k) == Some(v))
        && new
            .iter()
            .all(|(k, v)| old.contains_key(k) || (k == "type" && v == "stdio"))
}

/// Whether the entry under `neosian` there is ours.
pub fn ours(container: &Container, entry: &Value, context: &Context) -> bool {
    let book = Book::of(container.client, context);
    receipt::owns_in(&book, &container.file, &container.pointer(NEW), entry)
}

/// Plan one client from the files as they are now.
pub fn plan(client: &str, context: &Context) -> Plan {
    let client = static_client(client);
    let label = targets::mcp(client, context, "user").label;
    let mut plan = Plan {
        client,
        label,
        steps: vec![],
        stops: vec![],
        found: search::Found::default(),
        hosted: vec![],
    };
    if !installed(client, context) {
        return plan;
    }
    let receipt_path = receipt::path(client, context);
    if receipt::load(client, context).is_err() {
        plan.stops.push(Stop::Receipt(receipt_path));
    }
    if receipt::held(client, context) {
        plan.stops.push(Stop::Held);
    }
    for container in places::containers(client, context) {
        let Ok(entries) = container.entries() else {
            // A file that cannot be read stops the client only when its
            // text names a key (finding 1): it may hold our entry.
            if places::names_a_key(&container.file) {
                let stop = if container.client == "opencode" {
                    Stop::Unedited(container.file.clone())
                } else {
                    Stop::Unreadable(container.file.clone())
                };
                plan.stops.push(stop);
            }
            continue;
        };
        let new = entries.new;
        if let Some(new) = &new
            && !ours(&container, new, context)
        {
            plan.stops
                .push(Stop::Taken(container.file.clone(), container.pointer(NEW)));
        }
        let Some(old) = entries.old else {
            continue;
        };
        if old.get("url").is_some() {
            plan.hosted
                .push((container.file.clone(), container.pointer(OLD)));
            continue;
        }
        let remove_only = match &new {
            Some(new) if ours(&container, new, context) && equal(&old, new) => true,
            Some(new) if ours(&container, new, context) => {
                plan.stops
                    .push(Stop::Differ(container.file.clone(), container.pointer(OLD)));
                continue;
            }
            // Another server's `neosian` stands: the step is planned and
            // never made (the collision stops the client).
            Some(_) => false,
            None => false,
        };
        plan.steps.push(Step {
            container,
            entry: old,
            remove_only,
        });
    }
    // Writability and editability are checked now (finding 2): a client
    // that could only fail while writing is left alone with the reason.
    let taken: Vec<PathBuf> = plan
        .stops
        .iter()
        .filter_map(|s| match s {
            Stop::Taken(path, _) => Some(path.clone()),
            _ => None,
        })
        .collect();
    for step in plan
        .steps
        .iter()
        .filter(|s| !taken.contains(&s.container.file))
    {
        if let Err(key) = writes::check(step, OLD, NEW) {
            plan.stops
                .push(Stop::Unwritable(step.container.file.clone(), key));
        }
    }
    if plan.wanted() {
        plan.found = search::find(client, context);
    }
    plan
}

/// Every client's plan, a shared file stopping a client whose other reader
/// is installed and does not move in this run (§40.53 item 1).
pub fn plan_all(context: &Context, forced: bool) -> Vec<Plan> {
    let mut plans: Vec<Plan> = naming::CLIENTS.iter().map(|c| plan(c, context)).collect();
    let moving = |plan: &Plan| {
        if forced {
            plan.by_command()
        } else {
            plan.by_itself()
        }
    };
    for _ in 0..2 {
        let state: Vec<(&'static str, bool, bool)> = plans
            .iter()
            .map(|p| (p.client, moving(p), installed(p.client, context)))
            .collect();
        for plan in &mut plans {
            let shared: Vec<PathBuf> = plan
                .steps
                .iter()
                .filter(|s| s.container.shared())
                .map(|s| s.container.file.clone())
                .collect();
            for file in shared {
                for (other, moves, present) in &state {
                    let reads = ["claude-code", "muse-code"].contains(other);
                    let stop = Stop::Shared(file.clone(), other);
                    if *other != plan.client
                        && reads
                        && *present
                        && !moves
                        && !plan.stops.contains(&stop)
                    {
                        plan.stops.push(stop);
                    }
                }
            }
        }
    }
    plans
}

/// Whether a client is installed here: its configuration directory exists.
pub fn installed(client: &str, context: &Context) -> bool {
    context.directory(client).is_dir()
}
