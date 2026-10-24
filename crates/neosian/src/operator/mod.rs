//! Human operator doors over the same state interface as record and MCP.

pub mod audit;
pub mod erasure;
pub mod transfer;

use std::io::{self, Write};
use std::path::PathBuf;

use clap::Args;

use crate::conversation::{Error, failed};
use crate::remote::selection::Selection;
use crate::repr::repr;
use crate::store::grammar::Actor;

/// The store half of every operator command, resolved before construction.
#[derive(Args)]
pub struct Flags {
    /// FileStore root; defaults to the neosian home.
    #[arg(long)]
    root: Option<PathBuf>,
    /// State process URL; token from NEOSIAN_CLIENT_TOKEN.
    #[arg(long)]
    url: Option<String>,
    /// Postgres schema (Postgres only).
    #[arg(long)]
    schema: Option<String>,
    /// One JSON object on stdout.
    #[arg(long)]
    json: bool,
}

impl Flags {
    fn selection(&self) -> Result<Selection, String> {
        Selection::resolve_schema(
            self.root.as_deref(),
            self.url.as_deref(),
            self.schema.as_deref(),
        )
    }
}

fn actor(value: &str) -> Result<Actor, String> {
    Actor::parse(value)
        .map_err(|reason| format!("--actor {}: {reason} (e.g. cli:claude-code)", repr(value)))
}

fn finish(
    answer: Result<(), Error>,
    json: bool,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<u8> {
    match answer {
        Ok(()) => Ok(0),
        Err(Error::Io(error)) if error.kind() == io::ErrorKind::BrokenPipe => Err(error),
        Err(error) => failed(&error.text(), None, json, out, err),
    }
}

fn count(n: usize, noun: &str) -> String {
    format!("{n} {noun}{}", if n == 1 { "" } else { "s" })
}
