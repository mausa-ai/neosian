//! `neosian records`: turns stored as records (DESIGN §40.54 item 7). A
//! FileStore keeps them only after `records enable` writes its marker; a
//! Postgres store gets them from operator-installed records extension 1;
//! a server's store is enabled where it is served, never through its URL.

use std::io::{self, Write};

use clap::{Args, Subcommand};
use serde_json::json;

use super::{Flags, finish};
use crate::cli::usage;
use crate::conversation::Error;
use crate::remote::selection::Selection;
use crate::store::FileStore;
use crate::store::error::MemoryError;

#[derive(Subcommand)]
pub enum Command {
    /// Let this FileStore keep turns written in parts as records.
    Enable(Enable),
}

#[derive(Args)]
pub struct Enable {
    #[command(flatten)]
    flags: Flags,
}

pub fn run(command: &Command, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    match command {
        Command::Enable(args) => enable(args, out, err),
    }
}

fn enable(args: &Enable, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let json = args.flags.json;
    let selection = match args.flags.selection() {
        Ok(selection) => selection,
        Err(message) => return usage(json, &message, out, err),
    };
    let answer = (|| -> Result<(), Error> {
        let root = match &selection {
            Selection::File(root) => root,
            Selection::Postgres { .. } => {
                let why = "records on Postgres come from operator-installed records extension 1, not this command";
                return Err(MemoryError::configuration(why).into());
            }
            Selection::Remote { .. } | Selection::Profile(_) => {
                let why = "records are enabled on the store where it is served, not through a URL";
                return Err(MemoryError::configuration(why).into());
            }
        };
        let changed = FileStore::open(root)?.enable_records()?;
        let root = root.canonicalize()?;
        if json {
            let answer = json!({"root": root.to_string_lossy(), "records": {"version": 1}, "changed": changed});
            writeln!(out, "{answer}")?;
        } else if changed {
            writeln!(out, "records enabled at {}", root.display())?;
        } else {
            writeln!(out, "records already enabled at {}", root.display())?;
        }
        Ok(())
    })();
    finish(answer, json, out, err)
}
