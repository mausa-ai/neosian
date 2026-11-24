//! Operator commands for turns stored as records on a FileStore: enabling,
//! stale drafts, and migrating whole turns in place (DESIGN §40.54 item 21).
//! An explicit root must exist. Without one the commands use the neosian home.

use std::collections::HashSet;
use std::io::{self, Write};
use std::path::Path;

use clap::Subcommand;
use serde_json::json;

use super::{Flags, finish};
use crate::cli::usage;
use crate::conversation::Error;
use crate::conversation::ids::parse_conversation_id;
use crate::limits;
use crate::remote::selection::Selection;
use crate::store::FileStore;
use crate::store::error::MemoryError;

#[derive(Subcommand)]
pub enum Command {
    /// Let this FileStore keep turns written in parts as records.
    Enable(Flags),
    /// List stale drafts and staged restores, one JSON line per conversation.
    Drafts {
        /// Remove the stale items listed.
        #[arg(long)]
        discard: bool,
        #[command(flatten)]
        flags: Flags,
    },
    /// Turn whole turns into records, one JSON line per conversation.
    Migrate {
        /// Migrate this conversation; repeat for more. Default: every one.
        #[arg(long = "conversation", value_name = "ID")]
        conversations: Vec<String>,
        #[command(flatten)]
        flags: Flags,
    },
}

pub fn run(command: &Command, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let flags = match command {
        Command::Enable(flags) | Command::Drafts { flags, .. } | Command::Migrate { flags, .. } => {
            flags
        }
    };
    let selection = match flags.selection() {
        Ok(selection) => selection,
        Err(message) => return usage(flags.json, &message, out, err),
    };
    let answer = (|| {
        let root = match (&selection, command) {
            (Selection::File(root), _) => root,
            (Selection::Postgres { .. }, Command::Enable(_)) => {
                let why = "records on Postgres come from operator-installed records extension 1, not this command";
                return Err(MemoryError::configuration(why).into());
            }
            (_, Command::Enable(_)) => {
                let why = "records are enabled on the store where it is served, not through a URL";
                return Err(MemoryError::configuration(why).into());
            }
            (_, Command::Drafts { .. }) => {
                return Err(
                    MemoryError::configuration("records drafts requires a FileStore").into(),
                );
            }
            (_, Command::Migrate { .. }) => {
                let why = "records migrate requires a FileStore; Postgres has its own migration";
                return Err(MemoryError::configuration(why).into());
            }
        };
        if flags.root.is_some() && !root.is_dir() {
            let why = match command {
                Command::Enable(_) => {
                    "`records enable` enables a store that exists and creates none"
                }
                Command::Drafts { .. } => "`records drafts` requires an existing store",
                Command::Migrate { .. } => "`records migrate` requires an existing store",
            };
            return Err(MemoryError::configuration(format!(
                "no store at {}: {why}",
                root.display()
            ))
            .into());
        }
        let store = FileStore::open(root)?;
        match command {
            Command::Enable(_) => enable(&store, root, flags.json, out),
            Command::Drafts { discard, .. } => drafts(&store, *discard, out),
            Command::Migrate { conversations, .. } => migrate(&store, conversations, out),
        }
    })();
    finish(answer, flags.json, out, err)
}

fn enable(store: &FileStore, root: &Path, json: bool, out: &mut impl Write) -> Result<(), Error> {
    let changed = store.enable_records()?;
    let root = root.canonicalize()?;
    if json {
        let answer =
            json!({"root": root.to_string_lossy(), "records": {"version": 1}, "changed": changed});
        writeln!(out, "{answer}")?;
    } else if changed {
        writeln!(out, "records enabled at {}", root.display())?;
    } else {
        writeln!(out, "records already enabled at {}", root.display())?;
    }
    Ok(())
}

fn drafts(store: &FileStore, discard: bool, out: &mut impl Write) -> Result<(), Error> {
    let mut cursor = None;
    loop {
        let page = store.sweep_records(50, cursor.as_deref(), discard)?;
        for row in page.conversations {
            serde_json::to_writer(&mut *out, &row).map_err(io::Error::from)?;
            writeln!(out)?;
        }
        cursor = page.next_cursor;
        if cursor.is_none() {
            return Ok(());
        }
    }
}

/// Migrate the conversations named, or every one. The names are checked
/// first against the stored names, read in one pass that settles nothing,
/// so a name that is not a conversation of this store refuses the run with
/// no conversation touched.
fn migrate(store: &FileStore, named: &[String], out: &mut impl Write) -> Result<(), Error> {
    if !store.records_enabled()? {
        let why = "records are not enabled on this store; run `neosian records enable`";
        return Err(MemoryError::configuration(why).into());
    }
    let limits = limits::Records::load()?;
    let mut chosen = Vec::new();
    if named.is_empty() {
        chosen = store.conversations()?;
    } else {
        let stored = store.stored_conversations()?;
        let mut seen = HashSet::new();
        for id in named {
            parse_conversation_id(id)?;
            if stored.binary_search(id).is_err() {
                return Err(Error::Value(format!(
                    "no conversation {id} in this store; nothing was migrated"
                )));
            }
            if seen.insert(id) {
                chosen.push(id.clone());
            }
        }
    }
    for id in &chosen {
        let row = store.migrate_records(id, &limits)?;
        serde_json::to_writer(&mut *out, &row).map_err(io::Error::from)?;
        writeln!(out)?;
    }
    Ok(())
}
