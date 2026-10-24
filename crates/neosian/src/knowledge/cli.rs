//! The explicit filesystem boundary stays in the CLI; imported bytes are data.

use std::io::{self, Write};
use std::path::PathBuf;

use clap::Subcommand;

use super::{input, model, plan, read, rules};
use crate::memory::dispatch::Memory;
use crate::memory::grammar::Flags;
use crate::memory::mounts::{Refusal, resolve};
use crate::store::error::Error;

#[derive(Subcommand)]
pub enum Command {
    /// Retrieve current knowledge under an explicit review policy.
    Read {
        path: String,
        #[arg(long, value_enum, default_value = "effective")]
        policy: super::governance::Policy,
        #[command(flatten)]
        flags: Flags,
    },
    /// Prepare a review-state proposal; apply it with upkeep apply after review.
    Govern {
        path: String,
        #[arg(long)]
        file: PathBuf,
        #[arg(long)]
        expect_version: u64,
        #[arg(long)]
        reason: String,
        #[command(flatten)]
        flags: Flags,
    },
    /// Preview a Markdown or knowledge-bundle import; pause writers before --write.
    Import {
        input: PathBuf,
        /// Explicit mounted collection prefix, such as /memories/notes.
        #[arg(long)]
        to: String,
        #[arg(long, default_value = "markdown", value_parser = ["markdown", "bundle"])]
        format: String,
        /// Source label; filenames are stored relative to the selected input.
        #[arg(long)]
        source: Option<String>,
        #[arg(long, default_value = "fail", value_parser = ["fail", "skip", "replace"])]
        conflict: String,
        #[arg(long)]
        write: bool,
        #[command(flatten)]
        flags: Flags,
    },
    /// Inspect a collection or preview its definition from a JSON file.
    Collection {
        path: String,
        #[arg(long)]
        file: Option<PathBuf>,
        #[arg(long, requires = "file")]
        write: bool,
        #[command(flatten)]
        flags: Flags,
    },
    /// Check current documents against their optional collection rules.
    Validate {
        path: String,
        #[command(flatten)]
        flags: Flags,
    },
    /// Resolve explicit references using only currently mounted read authority.
    References {
        path: String,
        #[arg(long, value_enum, default_value = "all")]
        policy: super::governance::Policy,
        #[command(flatten)]
        flags: Flags,
    },
    /// Export current readable knowledge into a new bundle directory; pause writers first.
    Export {
        path: String,
        #[arg(long)]
        output: PathBuf,
        #[command(flatten)]
        flags: Flags,
    },
}

impl Command {
    fn flags(&self) -> &Flags {
        match self {
            Self::Read { flags, .. }
            | Self::Govern { flags, .. }
            | Self::Import { flags, .. }
            | Self::Collection { flags, .. }
            | Self::Validate { flags, .. }
            | Self::References { flags, .. }
            | Self::Export { flags, .. } => flags,
        }
    }
    fn path(&self) -> &str {
        match self {
            Self::Import { to, .. } => to,
            Self::Read { path, .. }
            | Self::Govern { path, .. }
            | Self::Collection { path, .. }
            | Self::Validate { path, .. }
            | Self::References { path, .. }
            | Self::Export { path, .. } => path,
        }
    }
}

pub fn run(command: &Command, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let flags = command.flags();
    let cwd = std::env::current_dir()?;
    let selection = match flags.select(&cwd) {
        Ok(selection) => selection,
        Err(message) => return crate::cli::usage(flags.json, &message, out, err),
    };
    let (mounts, actor) = match flags.selected_settings(&cwd, &selection) {
        Ok(settings) => settings,
        Err(refusal) => {
            let message = match refusal {
                Refusal::Usage(s) => s,
                Refusal::Memory(e) => e.message,
            };
            return crate::cli::usage(flags.json, &message, out, err);
        }
    };
    // Validate input and destination before opening a store or making remote calls.
    let prepared = (|| -> Result<_, Error> {
        let path = command.path();
        if !path.starts_with('/') || path.starts_with("//") || path.ends_with('/') {
            return Err(model::invalid(
                "expected an absolute mounted path without a trailing slash",
            ));
        }
        let (_, prefix) = resolve(&mounts, path)?;
        model::ordinary(prefix)?;
        let input = match command {
            Command::Import {
                input,
                format,
                source,
                ..
            } => Some(input::load(input, format, source.as_deref(), prefix)?),
            _ => None,
        };
        let definition = match command {
            Command::Collection {
                file: Some(file), ..
            } => Some(rules::Definition::from_json(&input::read(
                file,
                model::MAX_METADATA,
            )?)?),
            _ => None,
        };
        let governance = match command {
            Command::Govern {
                file,
                expect_version,
                reason,
                ..
            } => {
                if *expect_version == 0 {
                    return Err(model::invalid("expect-version must be positive"));
                }
                crate::upkeep::model::reason(reason)?;
                Some(super::govern::Change::parse(&input::read(
                    file,
                    model::MAX_METADATA,
                )?)?)
            }
            _ => None,
        };
        Ok((input, definition, governance))
    })();
    let (input, definition, governance) = match prepared {
        Ok(prepared) => prepared,
        Err(error) => return crate::cli::usage(flags.json, &super::text_error(error), out, err),
    };
    let result = (|| -> Result<u8, Error> {
        let memory = Memory {
            store: selection.open()?,
            mounts,
            actor,
        };
        let (report, code) = match command {
            Command::Read { path, policy, .. } => (super::govern::read(&memory, path, *policy)?, 0),
            Command::Govern {
                path,
                expect_version,
                reason,
                ..
            } => (
                serde_json::to_value(super::govern::prepare(
                    &memory,
                    path,
                    &governance.expect("prepared governance"),
                    *expect_version,
                    reason,
                )?)
                .expect("proposal serializes"),
                0,
            ),
            Command::Import {
                to,
                conflict,
                write,
                ..
            } => plan::import(
                &memory,
                to,
                input.expect("prepared input"),
                conflict,
                *write,
            )?,
            Command::Collection { path, write, .. } => {
                plan::collection(&memory, path, definition, *write)?
            }
            Command::Validate { path, .. } => read::validate(&memory, path)?,
            Command::References { path, policy, .. } => read::references(&memory, path, *policy)?,
            Command::Export { path, output, .. } => read::export(&memory, path, output)?,
        };
        writeln!(out, "{report}")?;
        Ok(code)
    })();
    crate::memory::operate::settle(result, flags.json, out, err)
}
