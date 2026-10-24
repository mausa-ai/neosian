//! CLI input files are explicit; stdout remains structured review data.

use std::io::{self, Write};
use std::path::PathBuf;

use clap::{Args, Subcommand};

use crate::knowledge::model::invalid;
use crate::memory::dispatch::Memory;
use crate::memory::grammar::Flags;
use crate::memory::mounts::Refusal;
use crate::store::error::Error;

use super::model::{MAX_PLAN, Proposal, Request, parse};

#[derive(Args)]
pub struct File {
    file: PathBuf,
    #[command(flatten)]
    flags: Flags,
}

#[derive(Subcommand)]
pub enum Command {
    /// Find bounded empty, duplicate, stale-review and inconsistent documents.
    Scan {
        #[arg(default_value = "/")]
        path: String,
        #[arg(long, default_value = "100", value_parser = clap::value_parser!(u32).range(1..=1000))]
        limit: u32,
        #[arg(long, default_value = "90", value_parser = clap::value_parser!(u32).range(1..=365_000))]
        stale_days: u32,
        #[command(flatten)]
        flags: Flags,
    },
    /// Prepare a guarded proposal from a format-1 request, without writing memory.
    Prepare(File),
    /// Compare a proposal with current state, including after an interrupted apply.
    Inspect(File),
    /// Preview a proposal. Pause writers and provide --write --approve DIGEST to apply.
    Apply {
        file: PathBuf,
        #[arg(long, requires = "approve")]
        write: bool,
        #[arg(long, requires = "write")]
        approve: Option<String>,
        #[command(flatten)]
        flags: Flags,
    },
}

pub fn run(command: &Command, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let (flags, file) = match command {
        Command::Scan { flags, .. } => (flags, None),
        Command::Prepare(f) | Command::Inspect(f) => (&f.flags, Some(&f.file)),
        Command::Apply { file, flags, .. } => (flags, Some(file)),
    };
    let result = (|| -> Result<u8, Error> {
        let raw = file
            .map(|p| crate::knowledge::input::read(p, MAX_PLAN))
            .transpose()?;
        let mut request = None;
        let mut proposal = None;
        if let Some(raw) = raw {
            if matches!(command, Command::Prepare(_)) {
                let parsed: Request = parse(&raw)?;
                parsed.validate()?;
                request = Some(parsed);
            } else {
                let parsed: Proposal = parse(&raw)?;
                parsed.validate()?;
                if let Command::Apply {
                    write: true,
                    approve,
                    ..
                } = command
                    && approve.as_deref() != Some(parsed.id.as_str())
                {
                    return Err(invalid("approval digest does not match the proposal"));
                }
                proposal = Some(parsed);
            }
        }
        let cwd = std::env::current_dir()?;
        let selection = flags.select(&cwd).map_err(invalid)?;
        let (mounts, actor) = flags
            .selected_settings(&cwd, &selection)
            .map_err(|r| match r {
                Refusal::Usage(s) => invalid(s),
                Refusal::Memory(e) => Error::Memory(e),
            })?;
        let memory = Memory {
            store: selection.open()?,
            mounts,
            actor,
        };
        let (report, code) = match command {
            Command::Scan {
                path,
                limit,
                stale_days,
                ..
            } => (super::scan(&memory, path, *limit as usize, *stale_days)?, 0),
            Command::Prepare(_) => (
                serde_json::to_value(super::prepare(&memory, &request.expect("parsed request"))?)
                    .expect("proposal serializes"),
                0,
            ),
            Command::Inspect(_) => (
                super::inspect(&memory, &proposal.expect("parsed proposal"))?,
                0,
            ),
            Command::Apply { write, approve, .. } => super::apply(
                &memory,
                &proposal.expect("parsed proposal"),
                *write,
                approve.as_deref(),
            )?,
        };
        writeln!(out, "{report}")?;
        Ok(code)
    })();
    crate::memory::operate::settle(result, flags.json, out, err)
}
