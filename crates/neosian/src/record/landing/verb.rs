//! `neosian record-land --home H [--spool DIR] [--url U]`, a hidden verb
//! (DESIGN §40.54 item 17.3; rulings D2, D4, D11 and D15 of the T1e1
//! plan), which a hook starts detached (`start.rs`, `spawn.rs`) and a
//! person or a contract may run in the foreground. It first takes one of
//! the home's two slots (`slots.rs`) and exits at once when both are
//! taken. It lands every queue of a home with the code a Stop lands its
//! own conversation with (`turn.rs`, `run.rs`): the home's `spool/` and
//! `profile-spool/`, or the one tree `--spool` names. Conversations go in
//! name order, each under its lock taken without waiting (one another
//! process holds is left). A turn lands only where this process's own
//! environment names its destination, as the state's descriptor names it:
//! the same root, the same DSN and schema, the same URL and token (the
//! URL `--url` hands over when the stored one lost its userinfo or query);
//! a profile turn's profile is resolved from its name and project, and
//! lands while its capture is on and its fingerprint is the turn's. A
//! destination whose record says it waits or is blocked is not asked, and
//! one blocked for six hours gets one attempt (`gate.rs`). A destination
//! whose answer stops a run (a wait or a block of 17.4) is written to its
//! record and not asked again in that run, and the conversations skipped
//! for it are counted; a conversation whose answer stops it, or that is
//! paused, waits for the next run. A `Retry-After` of at most 10 s is
//! waited out within the run's 30 s (`cap.rs`).
//! A pass that landed or held a turn is followed by another, for turns
//! closed meanwhile; the run ends after a pass that did neither, or at its
//! ten minutes (`cap.rs`). It prints one JSON line of what it did,
//! a queue it cannot list among its faults, and exits 1 only for a local
//! fault.

use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};

use clap::Args;
use serde_json::{Value, json};

use super::cap::{Cap, PASSED, Patience};
use super::files::entries;
use super::gate::{self, Gated};
use super::queue::Queue;
use super::serving::serving;
use super::slots;
use super::state::{Destination, State};

/// Land the closed turns queued under a neosian home.
#[derive(Args)]
pub struct Land {
    /// The neosian home whose `spool/` and `profile-spool/` are landed.
    #[arg(long)]
    pub(super) home: PathBuf,
    /// Land only this spool tree, as `neosian record --spool DIR` keeps it.
    #[arg(long)]
    pub(super) spool: Option<PathBuf>,
    /// The state process URL as the hook was given it, for turns whose
    /// stored URL lost its userinfo or query.
    #[arg(long)]
    pub(super) url: Option<String>,
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
    paused: Vec<String>,
    faults: Vec<Value>,
    /// The destinations and the conversations not asked again in this run.
    stops: Vec<Destination>,
    waits: Vec<String>,
    capped: bool,
}

impl Tally {
    /// What a pass lists is what the last pass found.
    fn next_pass(&mut self) {
        for list in [&mut self.left, &mut self.busy, &mut self.skipped] {
            list.clear();
        }
    }

    fn fault(&mut self, fault: Value) {
        if !self.faults.contains(&fault) {
            self.faults.push(fault);
        }
    }
}

pub fn run(args: &Land, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    run_on(args, &Cap::ten_minutes(), &Patience::new(), out, err)
}

/// `run` on the clock of `cap` and with the waits of `patience`.
pub fn run_on(
    args: &Land,
    cap: &Cap,
    patience: &Patience,
    out: &mut impl Write,
    _err: &mut impl Write,
) -> io::Result<u8> {
    let mut tally = Tally::default();
    let slot = slots::take(&args.home)?;
    if slot.is_some() {
        land(args, cap, patience, &mut tally);
    }
    let summary = json!({
        "slot": slot.as_ref().map(|slot| slot.number),
        "landed": tally.landed,
        "held": tally.held,
        "undocumented": tally.undocumented,
        "stopped": tally.stopped,
        "left": tally.left,
        "busy": tally.busy,
        "skipped": tally.skipped,
        "paused": tally.paused,
        "capped": tally.capped,
        "faults": tally.faults,
    });
    writeln!(out, "{summary}")?;
    Ok(u8::from(!tally.faults.is_empty()))
}

/// Passes over the trees until one lands and holds nothing, or the cap.
fn land(args: &Land, cap: &Cap, patience: &Patience, tally: &mut Tally) {
    let trees = match &args.spool {
        Some(tree) => vec![tree.clone()],
        None => vec![args.home.join("spool"), args.home.join("profile-spool")],
    };
    loop {
        let before = (tally.landed, tally.held);
        tally.next_pass();
        for tree in &trees {
            if let Err(error) = land_tree(tree, args, (cap, patience), tally) {
                let (tree, error) = (tree.display().to_string(), error.to_string());
                tally.fault(json!({"tree": tree, "error": error}));
            }
        }
        tally.capped = cap.passed();
        if tally.capped || (tally.landed, tally.held) == before {
            return;
        }
    }
}

/// The conversations with a pending directory under `root`, by name.
pub fn conversations(root: &Path) -> io::Result<Vec<String>> {
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

fn land_tree(
    root: &Path,
    args: &Land,
    (cap, patience): (&Cap, &Patience),
    tally: &mut Tally,
) -> io::Result<()> {
    let queue = Queue::new(root);
    for id in conversations(root)? {
        if cap.passed() {
            return Ok(());
        }
        if tally.waits.contains(&id) {
            continue;
        }
        // The states as they are now, read without the lock to choose whom
        // to serve; each landing reads them again under it.
        let mut states: Vec<State> = Vec::new();
        let listed = match entries(&queue.pending_dir(&id)) {
            Ok(listed) => listed,
            Err(error) => {
                let error = error.to_string();
                tally.fault(json!({"conversation_id": id, "error": error}));
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
            if tally.stops.contains(destination) {
                tally.skipped.push(id.clone());
                continue;
            }
            let url = args.url.as_deref();
            let Some(serving) = serving(destination, state, &args.home, url) else {
                tally.left.push(id.clone());
                continue;
            };
            let here = serving.here(cap, patience);
            match gate::land(&queue, &id, &here, &args.home, cap.now()) {
                Ok(Gated::Closed) => {
                    tally.skipped.push(id.clone());
                    tally.stops.push(destination.clone());
                }
                Ok(Gated::Busy) => tally.busy.push(id.clone()),
                Ok(Gated::Ran(run)) => {
                    tally.landed += run.landed.len();
                    tally.held += run.refused.len();
                    // Nothing more of a paused conversation in this run (E19).
                    if run.paused {
                        tally.paused.push(id.clone());
                        tally.waits.push(id.clone());
                    }
                    if let Some(error) = run.undocumented {
                        let error = error.text();
                        let entry = json!({"conversation_id": id, "error": error});
                        tally.undocumented.push(entry);
                    }
                    if let Some(stop) = run.stopped {
                        let message = stop.message();
                        let entry = json!({"conversation_id": id, "message": message});
                        tally.stopped.push(entry);
                        if message == PASSED {
                            return Ok(());
                        }
                        tally.waits.push(id.clone());
                        if stop.destination() {
                            tally.stops.push(destination.clone());
                        }
                    }
                }
                Err(error) => {
                    let error = error.text();
                    tally.fault(json!({"conversation_id": id, "error": error}));
                }
            }
        }
    }
    Ok(())
}
