//! `neosian record-land --home H [--spool DIR]`, a hidden verb (DESIGN
//! §40.54 item 17.3; rulings D2, D4 and D11 of the T1e1 plan): it lands
//! every queue of a home with the code a Stop lands its own conversation
//! with (`turn.rs`, `run.rs`). It serves the home's `spool/` and
//! `profile-spool/`, or the one tree `--spool` names. Conversations go in
//! name order, each under its lock taken without waiting (one another
//! process holds is left). A turn lands only where this process's own
//! environment names its destination, as the state's descriptor names it:
//! the same root, the same DSN and schema, the same URL and token; a
//! profile turn's profile is resolved from its name and project, and lands
//! while its capture is on and its fingerprint is the turn's. A destination
//! whose answer stops a run (a wait or a block of 17.4) is not asked again
//! in that run, and the conversations skipped for it are counted. It prints
//! one JSON line of what it did, a queue it cannot list among its faults,
//! and exits 1 only for a local fault. This slice runs it in the foreground; T1e1d starts it
//! detached from the hooks, with its slots and its ten-minute bound.

use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};

use clap::Args;
use serde_json::{Value, json};

use super::super::fingerprint::fingerprint;
use super::files::entries;
use super::queue::Queue;
use super::state::{Destination, State, describe};
use super::turn::{Here, land_queue};
use crate::memory::cli::DSN_ENV;
use crate::profile::model::CaptureMode;
use crate::remote::selection::{Selection, TOKEN_ENV};

/// Land the closed turns queued under a neosian home.
#[derive(Args)]
pub struct Land {
    /// The neosian home whose `spool/` and `profile-spool/` are landed.
    #[arg(long)]
    home: PathBuf,
    /// Land only this spool tree, as `neosian record --spool DIR` keeps it.
    #[arg(long)]
    spool: Option<PathBuf>,
}

/// What this process can land for one destination: the `Here` a Stop
/// builds from its own settings, built here from a queued turn's state.
struct Serving {
    selection: Selection,
    destination: Destination,
    fingerprint: Option<String>,
    scope: String,
    mount: String,
}

impl Serving {
    fn here(&self) -> Here<'_> {
        Here {
            selection: &self.selection,
            destination: self.destination.clone(),
            fingerprint: self.fingerprint.as_deref(),
            scope: &self.scope,
            mount: &self.mount,
            provenance: None,
        }
    }
}

#[derive(Default)]
struct Tally {
    landed: usize,
    held: usize,
    undocumented: Vec<Value>,
    stopped: Vec<Value>,
    left: Vec<String>,
    busy: Vec<String>,
    skipped: Vec<String>,
    faults: Vec<Value>,
}

pub fn run(args: &Land, out: &mut impl Write, _err: &mut impl Write) -> io::Result<u8> {
    let trees = match &args.spool {
        Some(tree) => vec![tree.clone()],
        None => vec![args.home.join("spool"), args.home.join("profile-spool")],
    };
    let mut tally = Tally::default();
    for tree in &trees {
        if let Err(error) = land_tree(tree, &args.home, &mut tally) {
            let (tree, error) = (tree.display().to_string(), error.to_string());
            tally.faults.push(json!({"tree": tree, "error": error}));
        }
    }
    let summary = json!({
        "landed": tally.landed,
        "held": tally.held,
        "undocumented": tally.undocumented,
        "stopped": tally.stopped,
        "left": tally.left,
        "busy": tally.busy,
        "skipped": tally.skipped,
        "faults": tally.faults,
    });
    writeln!(out, "{summary}")?;
    Ok(u8::from(!tally.faults.is_empty()))
}

/// The conversations with a pending directory under `root`, by name.
fn conversations(root: &Path) -> io::Result<Vec<String>> {
    let mut names = Vec::new();
    match fs::read_dir(root.join("pending")) {
        Ok(listing) => {
            for entry in listing {
                let entry = entry?;
                let name = entry.file_name().to_string_lossy().into_owned();
                if entry.file_type()?.is_dir() && !name.starts_with('.') {
                    names.push(name);
                }
            }
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    names.sort();
    Ok(names)
}

fn land_tree(root: &Path, home: &Path, tally: &mut Tally) -> io::Result<()> {
    let queue = Queue::new(root);
    let mut stopped: Vec<Destination> = Vec::new();
    for id in conversations(root)? {
        // The states as they are now, read without the lock to choose whom
        // to serve; each landing reads them again under it.
        let mut states: Vec<State> = Vec::new();
        let listed = match entries(&queue.pending_dir(&id)) {
            Ok(listed) => listed,
            Err(error) => {
                let error = error.to_string();
                tally
                    .faults
                    .push(json!({"conversation_id": id, "error": error}));
                continue;
            }
        };
        for (n, _) in listed {
            let file = Queue::part(&queue.pending_dir(&id), n, "state");
            if let Ok(state) = State::read(&file)
                && !states
                    .iter()
                    .any(|seen| seen.destination == state.destination)
            {
                states.push(state);
            }
        }
        // Profile and direct turns never share a conversation; one that
        // seems to is left for the hooks, which tell them apart.
        let profiles = states.iter().filter(|s| s.fingerprint.is_some()).count();
        if profiles != 0 && profiles != states.len() {
            tally.left.push(id);
            continue;
        }
        for state in &states {
            let Some(destination) = &state.destination else {
                continue;
            };
            if stopped.contains(destination) {
                tally.skipped.push(id.clone());
                continue;
            }
            let Some(serving) = serving(destination, state, home) else {
                tally.left.push(id.clone());
                continue;
            };
            match land_queue(&queue, &id, &serving.here()) {
                Ok(None) => tally.busy.push(id.clone()),
                Ok(Some(run)) => {
                    tally.landed += run.landed.len();
                    tally.held += run.refused.len();
                    if let Some(error) = run.undocumented {
                        let error = error.text();
                        tally
                            .undocumented
                            .push(json!({"conversation_id": id, "error": error}));
                    }
                    if let Some(stop) = run.stopped {
                        let message = stop.message();
                        tally
                            .stopped
                            .push(json!({"conversation_id": id, "message": message}));
                        if stop.destination() {
                            stopped.push(destination.clone());
                        }
                    }
                }
                Err(error) => {
                    let error = error.text();
                    tally
                        .faults
                        .push(json!({"conversation_id": id, "error": error}));
                }
            }
        }
    }
    Ok(())
}

/// The destination of a queued turn as this process can serve it, or
/// `None` when its environment does not name it (ruling D2).
fn serving(destination: &Destination, state: &State, home: &Path) -> Option<Serving> {
    let variable = |name: &str| std::env::var(name).ok().filter(|value| !value.is_empty());
    let (selection, fingerprint, scope, mount) = match destination {
        Destination::File { root } => (Selection::File(PathBuf::from(root)), None, None, None),
        Destination::Postgres { schema, .. } => {
            let dsn = variable(DSN_ENV)?;
            let schema = schema.clone();
            (Selection::Postgres { dsn, schema }, None, None, None)
        }
        Destination::Remote { url, .. } => {
            let (url, token) = (url.clone(), variable(TOKEN_ENV)?);
            (Selection::Remote { url, token }, None, None, None)
        }
        Destination::Profile { name, project, .. } => {
            let resolved = crate::profile::resolve::selected(Some(name), Path::new(project));
            let resolved = resolved.ok()??;
            let same_home =
                fs::canonicalize(&resolved.home).ok()? == fs::canonicalize(home).ok()?;
            let off = resolved.profile.capture.mode == CaptureMode::Off
                || (resolved.login.is_some()
                    && crate::login::local::capture_off(&resolved.home).unwrap_or(true));
            if !same_home || off {
                return None;
            }
            let current = fingerprint(&resolved).ok()?;
            let scope = resolved.profile.history.as_ref().map(|h| h.scope.clone());
            let mount = Some("history".to_string());
            (
                Selection::Profile(Box::new(resolved)),
                Some(current),
                scope,
                mount,
            )
        }
    };
    if describe(&selection) != *destination {
        return None;
    }
    Some(Serving {
        selection,
        destination: destination.clone(),
        fingerprint,
        scope: scope.or_else(|| state.scope.clone()).unwrap_or_default(),
        mount: mount.or_else(|| state.mount.clone()).unwrap_or_default(),
    })
}
