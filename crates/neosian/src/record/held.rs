//! `neosian record held` (DESIGN §40.54 item 17.6; ruling "Held selectors"
//! of the T1e2 plan). It lists the held turns of the home's `spool/` and
//! `profile-spool/`, or of the one tree `--spool` names, and the pending
//! turns there that this environment does not serve (`landing/aside.rs`).
//! `--discard ID|--all` deletes held turns and `--retry ID|--all` puts
//! them back at the end of their conversation's queue, in the order they
//! closed, each with its own outcome (`landing/requeue.rs`). An ID is
//! `<conversation id>/<n>`; one held in more than one tree is refused
//! until `--spool` names one. A retry sends nothing: the next hook event
//! that starts a lander for the turn's destination lands it. A profile
//! turn goes back only when its profile is found here with the policy it
//! was recorded under; for a turn held because its policy changed, that
//! is the profile a hook in this directory selects, or `--profile`. The
//! exit is 0 when every turn named did what was asked, 1 when one stays
//! held or a fault stopped the command, and 2 for a usage error. What it
//! prints is `held/print.rs`.

mod print;

use std::io::{self, Write};
use std::path::{Path, PathBuf};

use clap::Args;
use serde_json::json;

use super::fingerprint::provenance;
use super::landing::aside::{held, opening, unserved};
use super::landing::kept::cut;
use super::landing::queue::Queue;
use super::landing::requeue::{Ask, Chosen, Found, Outcome, Recorder, discard, retry};
use super::landing::serving::profiled;
use super::landing::state::{Destination, State, describe};
use super::profile::conversation_id;
use crate::cli::usage;
use crate::conversation::ids::check_conversation_id;
use crate::remote::selection::Selection;
use print::Row;

/// List held turns and the pending turns no environment here serves;
/// discard held turns or put them back in the queue.
#[derive(Args)]
pub struct Held {
    /// Delete the held turn ID (`<conversation id>/<n>`), or every held turn with --all.
    #[arg(long, value_name = "ID", num_args = 0..=1, default_missing_value = "", conflicts_with = "retry")]
    pub discard: Option<String>,
    /// Put the held turn ID back at the end of its conversation's queue, or every held turn with --all.
    #[arg(long, value_name = "ID", num_args = 0..=1, default_missing_value = "")]
    pub retry: Option<String>,
    /// With --discard or --retry: every held turn.
    #[arg(long)]
    pub all: bool,
    /// With --retry: also put back a turn whose earlier answer could not be read; the store may then hold it twice.
    #[arg(long)]
    pub allow_duplicate: bool,
    /// With --retry: the profile a turn held because its policy changed must meet again (default: the one a hook here selects).
    #[arg(long)]
    pub profile: Option<String>,
    /// Only this spool tree, as `neosian record --spool DIR` keeps it (default: the home's spool/ and profile-spool/).
    #[arg(long)]
    pub spool: Option<PathBuf>,
    /// One JSON object on stdout.
    #[arg(long)]
    pub json: bool,
}

/// What was asked of `id`, or why the arguments do not say.
fn asked(args: &Held) -> Result<Option<(&'static str, &str)>, &'static str> {
    let asked = match (&args.discard, &args.retry) {
        (Some(id), _) => Some(("discard", id.as_str())),
        (None, Some(id)) => Some(("retry", id.as_str())),
        (None, None) => None,
    };
    let retrying = args.allow_duplicate || args.profile.is_some();
    match asked {
        None if args.all || retrying => {
            Err("--all, --allow-duplicate and --profile go with --discard or --retry")
        }
        Some(("discard", _)) if retrying => Err("--allow-duplicate and --profile go with --retry"),
        Some((_, "")) if !args.all => Err("name a held turn as <conversation id>/<n>, or --all"),
        Some((_, id)) if !id.is_empty() && args.all => Err("name one held turn or --all, not both"),
        Some((_, id)) if !id.is_empty() && parse(id).is_none() => {
            Err("a held turn is named <conversation id>/<n>, as `neosian record held` lists it")
        }
        _ => Ok(asked),
    }
}

pub fn run(args: &Held, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let asked = match asked(args) {
        Ok(asked) => asked,
        Err(message) => return usage(args.json, message, out, err),
    };
    let done = crate::home::home().and_then(|home| {
        let trees = match &args.spool {
            Some(tree) => vec![tree.clone()],
            None => vec![home.join("spool"), home.join("profile-spool")],
        };
        match asked {
            None => list(&trees, &home, args.json, out),
            Some((verb, id)) => act(args, verb, id, &trees, &home, out),
        }
    });
    done.or_else(|error| {
        match args.json {
            true => writeln!(out, "{}", json!({"error": error.to_string()}))?,
            false => writeln!(err, "error: {error}")?,
        }
        Ok(1)
    })
}

/// `<conversation id>/<n>`, `n` without leading zeros.
fn parse(name: &str) -> Option<(&str, u64)> {
    let (id, n) = name.rsplit_once('/')?;
    let canonical = !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit());
    let canonical = canonical && (n == "0" || !n.starts_with('0'));
    check_conversation_id(id).ok()?;
    Some((id, n.parse().ok().filter(|_| canonical)?))
}

fn list(trees: &[PathBuf], home: &Path, json: bool, out: &mut impl Write) -> io::Result<u8> {
    let (mut kept, mut waiting) = (Vec::new(), Vec::new());
    for tree in trees {
        kept.extend(held(tree)?);
        waiting.extend(unserved(tree, home)?);
    }
    print::list(&kept, &waiting, json, out)?;
    Ok(0)
}

/// A held turn as chosen: its tree, conversation, number and opening line.
type Target = (PathBuf, String, u64, Option<String>);

/// The held turns `id` names (one, unless it is ambiguous) or every held
/// turn, each with the opening line it begins with now, in the order they
/// closed within each conversation.
fn chosen(id: &str, trees: &[PathBuf]) -> io::Result<Vec<Target>> {
    let mut found = Vec::new();
    let file = |tree: &Path, id: &str, n| Queue::part(&Queue::new(tree).held_dir(id), n, "jsonl");
    if let Some((id, n)) = parse(id) {
        for tree in trees.iter().filter(|tree| file(tree, id, n).is_file()) {
            found.push((
                tree.clone(),
                id.to_string(),
                n,
                opening(&file(tree, id, n))?,
            ));
        }
        return Ok(found);
    }
    for tree in trees {
        let mut entries = held(tree)?;
        entries.sort_by_key(|entry| (entry.id.clone(), entry.closed_at.clone(), entry.n));
        for entry in entries {
            let line = opening(&file(tree, &entry.id, entry.n)).unwrap_or_default();
            found.push((entry.tree, entry.id, entry.n, line));
        }
    }
    Ok(found)
}

fn act(
    args: &Held,
    verb: &'static str,
    id: &str,
    trees: &[PathBuf],
    home: &Path,
    out: &mut impl Write,
) -> io::Result<u8> {
    let targets = chosen(id, trees)?;
    let shown = |trees: Vec<&PathBuf>| {
        let trees: Vec<String> = trees
            .iter()
            .map(|tree| tree.display().to_string())
            .collect();
        trees.join(", ")
    };
    if !id.is_empty() && targets.len() != 1 {
        let message = match targets.len() {
            0 => format!("no held turn {id} in {}", shown(trees.iter().collect())),
            _ => format!(
                "{id} is held in more than one spool tree ({}); name one with --spool",
                shown(targets.iter().map(|target| &target.0).collect())
            ),
        };
        return Err(io::Error::other(message));
    }
    let cwd = std::env::current_dir()?;
    let find = |state: &State| found(state, args.profile.as_deref(), &cwd, home);
    let ask = Ask {
        duplicate: args.allow_duplicate,
        find: &find,
    };
    let mut rows = Vec::new();
    for (tree, id, n, line) in &targets {
        let recorder = match &args.spool {
            Some(_) => Recorder::Unknown,
            None if *tree == home.join("profile-spool") => Recorder::Profile,
            None => Recorder::Direct,
        };
        let target = Chosen {
            tree,
            recorder,
            id,
            n: *n,
            line: line.as_deref(),
        };
        let outcome = match verb {
            "discard" => discard(&target),
            _ => retry(&target, &ask, |_| Ok(())),
        };
        let mut row = Row::new(tree, format!("{id}/{n}"));
        match outcome {
            Ok(Outcome::Retried { n, bytes }) => {
                (row.outcome, row.bytes, row.queued) =
                    ("retried", Some(bytes), Some(format!("{id}/{n}")));
            }
            Ok(Outcome::Discarded { bytes }) => {
                (row.outcome, row.bytes) = ("discarded", Some(bytes))
            }
            Ok(Outcome::Stays { code, why }) => (row.outcome, row.why) = (code, Some(why)),
            Err(error) => (row.outcome, row.why) = ("failed", Some(cut(&error.to_string()))),
        }
        rows.push(row);
    }
    print::outcomes(verb, &rows, args.json, out)?;
    Ok(u8::from(rows.iter().any(|row| row.bytes.is_none())))
}

/// The profile of a profile turn as found here: by the name and project
/// its state names, or, for a turn held because its policy changed, as a
/// hook in `cwd` selects one (or `explicit`). Its capture must be on and
/// it must be served from this home (`serving.rs`).
fn found(state: &State, explicit: Option<&str>, cwd: &Path, home: &Path) -> Result<Found, String> {
    let (name, project) = match &state.destination {
        Some(Destination::Profile { name, project, .. }) => (name.clone(), project.clone()),
        Some(_) => return Err("its state names a policy for a direct destination".into()),
        None => {
            let selected = crate::profile::resolve::selected(explicit, cwd)?;
            let selected =
                selected.ok_or("no profile is selected here; name one with --profile")?;
            (selected.name, cwd.to_string_lossy().into_owned())
        }
    };
    let (resolved, fingerprint) = profiled(&name, &project, home).ok_or_else(|| {
        format!("profile {name} is not served from this home with its capture on")
    })?;
    let profile = &resolved.profile;
    let scope = profile
        .history
        .as_ref()
        .map(|history| history.scope.clone());
    let scope = scope.ok_or("its profile keeps no history")?;
    let prefix = profile.conversation_prefix();
    let conversation_id = conversation_id(
        &prefix,
        &resolved.installation_id,
        &state.agent,
        &state.session,
    );
    let provenance = provenance(profile, &state.session).map_err(|error| error.to_string())?;
    let project = std::path::absolute(&resolved.project).unwrap_or(resolved.project.clone());
    Ok(Found {
        fingerprint,
        conversation_id,
        scope,
        mount: "history".into(),
        provenance,
        project: project.to_string_lossy().into_owned(),
        destination: describe(&Selection::Profile(Box::new(resolved))),
    })
}
