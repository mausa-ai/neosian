//! `neosian memory` (`memory/cli.py`, DESIGN §14.2): the shell as a
//! transport over the memory ladder. stdout carries the artifact and
//! stderr the guidance; exit 0 is success, 1 a command that ran and
//! failed, 2 an invocation that was wrong, with nothing built. `--json`
//! prints the memory tool's envelope, one object.

use std::env;
use std::io::{self, Write};

use serde_json::json;

use super::dispatch::{Call, Memory, Outcome, dispatch};
use super::grammar::Command;
use super::mounts::Refusal;
use super::operate;
use crate::cli::usage;
use crate::home::home;
use crate::store::FileStore;

pub const DSN_ENV: &str = "NEOSIAN_POSTGRES_DSN";
pub const NEEDS_PYTHON: &str = "NEOSIAN_POSTGRES_DSN names a Postgres store, and the binary reads \
                            the file store alone: needs the Python shell: `uv tool install neosian`";

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
    let (mounts, actor) = match command.settings(&env::current_dir()?) {
        Ok(settled) => settled,
        Err(Refusal::Usage(message)) => return usage(json, &message, out, err),
        Err(Refusal::Memory(error)) => {
            writeln!(err, "error: [{}] {}", error.code, error.message)?;
            return Ok(2);
        }
    };
    let text = command.payload()?;
    let root = match &flags.root {
        Some(root) => root.clone(),
        None => home()?,
    };
    let memory = Memory {
        store: FileStore::open(&root)?,
        mounts,
        actor,
    };
    let call = match command {
        Command::View {
            path, view_range, ..
        } => {
            let range = view_range.as_deref().map(|range| (range[0], range[1]));
            Call::View { path, range }
        }
        Command::Create { path, .. } => Call::Create {
            path,
            content: &text,
        },
        Command::StrReplace { path, old_str, .. } => Call::StrReplace {
            path,
            old: old_str,
            new: &text,
        },
        Command::Insert {
            path, insert_line, ..
        } => Call::Insert {
            path,
            line: *insert_line,
            text: &text,
        },
        Command::Delete { path, .. } => Call::Delete { path },
        Command::Rename {
            old_path, new_path, ..
        } => Call::Rename {
            old: old_path,
            new: new_path,
        },
        Command::Versions { path, limit, .. } => {
            let answered = operate::versions(&memory, path, *limit as usize, json, out);
            return operate::settle(answered, json, out, err);
        }
        Command::Redact { path, all, .. } => {
            let answered = operate::redact(&memory, path, *all, json, out);
            return operate::settle(answered, json, out, err);
        }
        Command::Revert { path, version, .. } => {
            let answered = operate::revert(&memory, path, *version as u64, json, out, err);
            return operate::settle(answered, json, out, err);
        }
    };
    render(&dispatch(&memory, &call)?, json, out, err)
}
