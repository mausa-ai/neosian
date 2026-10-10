//! `neosian memory` (`memory/cli_grammar.py`, `cli.py`; DESIGN §14.2): the
//! shell as a transport over the memory commands. stdout carries the
//! artifact and stderr the guidance; exit 0 is success, 1 a command that
//! ran and failed, 2 an invocation that was wrong, with nothing built.
//! `--json` prints the memory tool's envelope, one object.

use std::env;
use std::io::{self, Write};
use std::path::{Path, PathBuf};

use clap::{Args, Subcommand};
use serde_json::json;

use super::commands;
use super::dispatch::{Memory, Outcome, settle};
use super::mounts::{Mount, Refusal};
use super::operate;
use crate::cli::usage;
use crate::home::{home, project_mounts};
use crate::repr::repr;
use crate::store::FileStore;
use crate::store::grammar::parse_actor;

const DSN_ENV: &str = "NEOSIAN_POSTGRES_DSN";
const SCOPE_ENV: &str = "NEOSIAN_SCOPE";
/// Where the `--scope` sugar mounts: the path a trained memory tool roots at.
const SUGAR_MOUNT_PATH: &str = "memories";
const NEEDS_PYTHON: &str = "NEOSIAN_POSTGRES_DSN names a Postgres store, and the binary reads \
                            the file store alone: needs the Python shell: `uv tool install neosian`";

/// The store flags every memory command takes.
#[derive(Args)]
pub struct Flags {
    /// FileStore root directory (created on start; default: the home,
    /// ~/.neosian or $NEOSIAN_HOME)
    #[arg(long)]
    root: Option<PathBuf>,
    /// single read-write mount of SCOPE at /memories
    #[arg(long)]
    scope: Option<String>,
    /// explicit mount; repeatable (ro = read-only, eo = edit-only:
    /// existing documents only)
    #[arg(long, value_name = "scope=...,path=...[,ro|,eo]")]
    mount: Vec<String>,
    /// who writes: <kind>:<id>[/...], recorded on every row
    #[arg(long, default_value = "cli:local")]
    actor: String,
    /// print the result as JSON on stdout
    #[arg(long)]
    json: bool,
}

#[derive(Subcommand)]
pub enum Command {
    /// print a document, a directory listing, or / (the index)
    View {
        /// virtual path
        #[arg(default_value = "/")]
        path: String,
        /// 1-based inclusive line range; END -1 means end of document
        #[arg(long, num_args = 2, value_names = ["START", "END"], allow_negative_numbers = true)]
        view_range: Option<Vec<i64>>,
        #[command(flatten)]
        flags: Flags,
    },
    /// list a document's version rows, newest first
    Versions {
        /// virtual document path
        path: String,
        /// most recent rows to show
        #[arg(
            long,
            value_name = "N",
            default_value_t = 50,
            allow_negative_numbers = true
        )]
        limit: i64,
        #[command(flatten)]
        flags: Flags,
    },
}

impl Command {
    fn flags(&self) -> &Flags {
        match self {
            Self::View { flags, .. } | Self::Versions { flags, .. } => flags,
        }
    }

    /// The guards the grammar owes beyond its shape, each a pure check.
    fn guard(&self) -> Result<(), Refusal> {
        match self {
            Self::Versions { limit, .. } if *limit < 1 => {
                Err(Refusal::Usage("--limit must be at least 1".to_string()))
            }
            _ => Ok(()),
        }
    }
}

/// What the store flags resolve to, nothing built: the mounts they name
/// (the flags, else `NEOSIAN_SCOPE` as the sugar mount, else the working
/// directory's project layout), the actor held to its grammar.
fn settings(flags: &Flags, cwd: &Path) -> Result<Vec<Mount>, Refusal> {
    if flags.scope.is_some() && !flags.mount.is_empty() {
        let sugar = "--scope is the single-mount sugar; use --mount for multi-mount";
        return Err(Refusal::Usage(sugar.to_string()));
    }
    let named = env::var(SCOPE_ENV).ok().filter(|scope| !scope.is_empty());
    let mounts = match flags.scope.as_ref().or(named.as_ref()) {
        _ if !flags.mount.is_empty() => flags
            .mount
            .iter()
            .map(|token| Mount::parse(token))
            .collect::<Result<Vec<_>, _>>()?,
        Some(scope) => vec![Mount::new(scope, SUGAR_MOUNT_PATH, (false, false), "")?],
        None => project_mounts(cwd)?,
    };
    let twice = |mount: &Mount| {
        mounts
            .iter()
            .filter(|other| other.path == mount.path)
            .count()
            > 1
    };
    if mounts.iter().any(twice) {
        return Err(Refusal::Usage("mount paths must be unique".to_string()));
    }
    if let Err(reason) = parse_actor(&flags.actor) {
        let actor = repr(&flags.actor);
        return Err(Refusal::Usage(format!(
            "--actor {actor}: {reason} (e.g. cli:claude-code)"
        )));
    }
    Ok(mounts)
}

/// A command's answer on the streams: the envelope under `--json`, else
/// the data on stdout and the guidance on stderr.
fn render(
    outcome: &Outcome,
    json: bool,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<u8> {
    let hint = outcome.reminder.as_deref().filter(|hint| !hint.is_empty());
    if json {
        let mut envelope = match &outcome.result {
            Ok(data) => json!({"success": true, "data": data}),
            Err(error) => json!({"success": false, "error": error}),
        };
        if let Some(hint) = hint {
            envelope["system_reminder"] = hint.into();
        }
        writeln!(out, "{envelope}")?;
    } else {
        match &outcome.result {
            Ok(data) if data.is_empty() => {}
            Ok(data) => writeln!(out, "{}", data.strip_suffix('\n').unwrap_or(data))?,
            Err(error) => writeln!(err, "error: {error}")?,
        }
        if let Some(hint) = hint {
            writeln!(err, "hint: {hint}")?;
        }
    }
    Ok(u8::from(outcome.result.is_err()))
}

/// Parse and execute one memory command; nothing is built on exit 2.
pub fn run(command: &Command, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let flags = command.flags();
    let json = flags.json;
    if env::var_os(DSN_ENV).is_some_and(|dsn| !dsn.is_empty()) {
        return operate::fail(&Outcome::fail(NEEDS_PYTHON), json, out, err);
    }
    let cwd = env::current_dir()?;
    let settled = settings(flags, &cwd).and_then(|mounts| command.guard().map(|()| mounts));
    let mounts = match settled {
        Ok(mounts) => mounts,
        Err(Refusal::Usage(message)) => return usage(json, &message, out, err),
        Err(Refusal::Memory(error)) => {
            writeln!(err, "error: [{}] {}", error.code, error.message)?;
            return Ok(2);
        }
    };
    let root = match &flags.root {
        Some(root) => root.clone(),
        None => home()?,
    };
    let memory = Memory {
        store: FileStore::open(&root)?,
        mounts,
    };
    match command {
        Command::View {
            path, view_range, ..
        } => {
            let range = view_range.as_deref().map(|range| (range[0], range[1]));
            let outcome = settle(commands::view(&memory, path, range))?;
            render(&outcome, json, out, err)
        }
        Command::Versions { path, limit, .. } => {
            let answered = operate::versions(&memory, path, *limit as usize, json, out);
            operate::settle(answered, json, out, err)
        }
    }
}
