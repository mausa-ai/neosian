//! Skill management shares storage selection with memory and keeps filesystem input explicit.

use std::io::{self, Write};
use std::path::PathBuf;

use clap::{Args, Subcommand};

use crate::knowledge::input::read;
use crate::knowledge::model::{MAX_BODY, invalid};
use crate::memory::dispatch::Memory;
use crate::memory::grammar::Flags;
use crate::memory::mounts::Refusal;
use crate::store::error::Error;

#[derive(Args)]
pub struct Target {
    path: String,
    #[command(flatten)]
    flags: Flags,
}

#[derive(Args)]
pub struct Selection {
    path: String,
    #[arg(long)]
    write: bool,
    #[command(flatten)]
    flags: Flags,
}

#[derive(Subcommand)]
pub enum Command {
    /// Discover currently selected skills and visible validation failures.
    Discover {
        #[command(flatten)]
        flags: Flags,
    },
    /// Load current instructions, honoring profile selection and revision pins.
    Load(Target),
    /// Inspect a permitted skill's current content, source and dependency status.
    Inspect(Target),
    /// Check a stored skill, its dependencies and supporting text.
    Validate(Target),
    /// Preview a Markdown or directory package import. Pause writers before --write.
    Import {
        input: PathBuf,
        #[arg(long)]
        to: String,
        #[arg(long)]
        replace: bool,
        #[arg(long)]
        write: bool,
        #[command(flatten)]
        flags: Flags,
    },
    /// Export current skill and text files into a new directory. Pause writers first.
    Export {
        path: String,
        #[arg(long)]
        output: PathBuf,
        #[command(flatten)]
        flags: Flags,
    },
    /// Preview replacement root instructions, preserving metadata for unmarked input.
    Edit {
        path: String,
        #[arg(long)]
        file: PathBuf,
        #[arg(long, value_parser = clap::value_parser!(u64).range(1..))]
        expect_version: u64,
        #[arg(long)]
        write: bool,
        #[command(flatten)]
        flags: Flags,
    },
    /// Compare retained root revisions as one exact textual replacement hunk.
    Diff {
        path: String,
        #[arg(long, value_parser = clap::value_parser!(u64).range(1..))]
        from: u64,
        #[arg(long, value_parser = clap::value_parser!(u64).range(1..))]
        to: Option<u64>,
        #[command(flatten)]
        flags: Flags,
    },
    /// Preview restoring a retained root revision as a new version.
    Revert {
        path: String,
        #[arg(long, value_parser = clap::value_parser!(u64).range(1..))]
        version: u64,
        #[arg(long, value_parser = clap::value_parser!(u64).range(1..))]
        expect_version: u64,
        #[arg(long)]
        write: bool,
        #[command(flatten)]
        flags: Flags,
    },
    /// Preview selecting a reference in an explicit profile, without copying its body.
    Select(Selection),
    /// Preview removing a profile selection and its pin.
    Unselect(Selection),
    /// Preview guarding a selected skill at its current source/version/hash; repeat to update.
    Pin(Selection),
    /// Preview allowing a selected skill to follow its current version again.
    Unpin(Selection),
}

impl Command {
    fn flags(&self) -> &Flags {
        match self {
            Self::Discover { flags }
            | Self::Import { flags, .. }
            | Self::Export { flags, .. }
            | Self::Edit { flags, .. }
            | Self::Diff { flags, .. }
            | Self::Revert { flags, .. } => flags,
            Self::Load(t) | Self::Inspect(t) | Self::Validate(t) => &t.flags,
            Self::Select(t) | Self::Unselect(t) | Self::Pin(t) | Self::Unpin(t) => &t.flags,
        }
    }
}

pub fn run(command: &Command, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let flags = command.flags();
    let result = (|| -> Result<u8, Error> {
        let local = match command {
            Command::Select(s) => Some((s, "select")),
            Command::Unselect(s) => Some((s, "unselect")),
            Command::Unpin(s) => Some((s, "unpin")),
            _ => None,
        };
        if let Some((s, action)) = local {
            flags.local_profile().map_err(invalid)?;
            let (report, code) =
                super::profile::change(flags.profile.as_deref(), &s.path, action, s.write, None)?;
            writeln!(out, "{report}")?;
            return Ok(code);
        }
        // File input is checked before a store is opened or any remote call is made.
        let input = match command {
            Command::Import { input, to, .. } => Some(super::package::load(input, to)?),
            _ => None,
        };
        let edited = match command {
            Command::Edit { file, .. } => Some(read(file, MAX_BODY)?),
            _ => None,
        };
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
            Command::Discover { .. } => super::discover(&memory)?,
            Command::Load(t) => super::load(&memory, &t.path)?,
            Command::Inspect(t) => super::inspect(&memory, &t.path, false)?,
            Command::Validate(t) => super::inspect(&memory, &t.path, true)?,
            Command::Import {
                to, replace, write, ..
            } => super::package::import(
                &memory,
                to,
                input.expect("prepared package"),
                *replace,
                *write,
            )?,
            Command::Export { path, output, .. } => super::package::export(&memory, path, output)?,
            Command::Edit {
                path,
                expect_version,
                write,
                ..
            } => super::revision::edit(
                &memory,
                path,
                edited.as_deref(),
                None,
                *expect_version,
                *write,
            )?,
            Command::Diff { path, from, to, .. } => {
                super::revision::diff(&memory, path, *from, *to)?
            }
            Command::Revert {
                path,
                version,
                expect_version,
                write,
                ..
            } => {
                super::revision::edit(&memory, path, None, Some(*version), *expect_version, *write)?
            }
            Command::Pin(s) => super::profile::change(
                flags.profile.as_deref(),
                &s.path,
                "pin",
                s.write,
                Some(super::profile::pin(&memory, &s.path)?),
            )?,
            _ => unreachable!("local profile changes handled above"),
        };
        writeln!(out, "{report}")?;
        Ok(code)
    })();
    crate::memory::operate::settle(result, flags.json, out, err)
}
