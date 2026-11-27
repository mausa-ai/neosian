//! Archives are FileStore roots; every selected target is preflighted
//! before restoring the first unit (DESIGN §26). An import into a server
//! that declares turn records version 1 restores each conversation that
//! holds a turn in parts (`restore.rs`, DESIGN §40.54 item 22); a memory
//! scope, a conversation without turns and every other target go whole
//! (`legacy.rs`), as before.

#[cfg(test)]
mod driver_tests;
mod legacy;
mod pack;
#[cfg(test)]
mod pack_tests;
mod restore;
mod stream;
#[cfg(test)]
mod tests;
mod trail;

use std::io::{self, Write};
use std::path::{Component, PathBuf};

use clap::Args;
use serde_json::{Value, json};

use self::restore::Planned;
use self::trail::Trail;
use super::{Flags, count, finish};
use crate::cli::usage;
use crate::conversation::ids::check_conversation_id;
use crate::conversation::{Error, Refused};
use crate::state::State;
use crate::state::records::{Records, Source};
use crate::store::FileStore;
use crate::store::error::MemoryError;
use crate::store::grammar::check_scope;

#[derive(Args)]
pub struct Transfer {
    /// The archive directory, itself a FileStore root.
    #[arg(value_name = "DIR")]
    directory: PathBuf,
    #[command(flatten)]
    flags: Flags,
    /// Only this scope (repeatable).
    #[arg(long = "scope")]
    scopes: Vec<String>,
    /// Only this conversation (repeatable).
    #[arg(long = "conversation")]
    conversations: Vec<String>,
}

#[derive(Default)]
struct Unit {
    kind: &'static str,
    name: String,
    documents: usize,
    versions: usize,
    redactions: usize,
    turns: usize,
    projections: usize,
}

impl Unit {
    fn json(&self) -> Value {
        json!({
            "kind": self.kind, "name": self.name, "documents": self.documents,
            "versions": self.versions, "redactions": self.redactions,
            "turns": self.turns, "projections": self.projections,
        })
    }
}

pub fn run(
    args: &Transfer,
    import: bool,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<u8> {
    // pathlib drops redundant separators and dot components, keeping `..`.
    let directory: PathBuf = args
        .directory
        .components()
        .filter(|part| *part != Component::CurDir)
        .collect();
    let directory = if directory.as_os_str().is_empty() {
        PathBuf::from(".")
    } else {
        directory
    };
    let settings = || -> Result<_, String> {
        let selection = args.flags.selection()?;
        for scope in &args.scopes {
            check_scope(scope).map_err(|e| format!("--scope: {e}"))?;
        }
        for id in &args.conversations {
            check_conversation_id(id).map_err(|e| format!("--conversation: {e}"))?;
        }
        if import && !directory.is_dir() {
            return Err(format!("DIR: {} is not a directory", directory.display()));
        }
        Ok(selection)
    };
    let selection = match settings() {
        Ok(selection) => selection,
        Err(message) => return usage(args.flags.json, &message, out, err),
    };
    let verb = if import { "import" } else { "export" };
    let answer = (|| {
        // The concrete store, so a local trail is read by its own pages.
        let opened = Source::open(&selection)?;
        let store = opened.state();
        let archive = FileStore::open(&directory)?;
        let (source, target, trail): (&dyn State, &dyn State, _) = if import {
            (&archive, store, Trail::File(&archive))
        } else {
            (store, &archive, Trail::of(&opened))
        };
        // Only a server's records take a restore in parts.
        let keeper = match &opened {
            Source::Remote(_) if import => opened.keeper()?,
            _ => None,
        };
        let records = keeper.as_ref().map(|keeper| keeper as &dyn Records);
        let selected = (&args.scopes[..], &args.conversations[..]);
        let units = transfer(source, target, &trail, records, selected)?;
        if args.flags.json {
            // canonicalize exists after opening, and the public path has no
            // Windows extended-length prefix (Path.resolve's spelling).
            let resolved = directory.canonicalize()?;
            let path = resolved.to_string_lossy();
            let archive = if let Some(unc) = path.strip_prefix(r"\\?\UNC\") {
                format!(r"\\{unc}")
            } else {
                path.strip_prefix(r"\\?\").unwrap_or(&path).to_string()
            };
            writeln!(
                out,
                "{}",
                json!({"verb":verb,"archive":archive,"client":store.client(),"units":units.iter().map(Unit::json).collect::<Vec<_>>()})
            )?;
        } else if units.is_empty() {
            writeln!(out, "nothing to {verb}")?;
        } else {
            for unit in &units {
                let counts = if unit.kind == "scope" {
                    vec![
                        count(unit.documents, "document"),
                        count(unit.versions, "version"),
                        count(unit.redactions, "redaction"),
                    ]
                } else {
                    vec![
                        count(unit.turns, "turn"),
                        count(unit.projections, "projection"),
                    ]
                };
                writeln!(
                    out,
                    "{:<12} {}  {}",
                    unit.kind,
                    unit.name,
                    counts.join("  ")
                )?;
            }
            let scopes = units.iter().filter(|u| u.kind == "scope").count();
            let place = if import { "from" } else { "to" };
            writeln!(
                out,
                "{verb}ed {}, {} {place} {}",
                count(scopes, "scope"),
                count(units.len() - scopes, "conversation"),
                directory.display()
            )?;
        }
        Ok(())
    })();
    finish(answer, args.flags.json, out, err)
}

fn names(mut names: Vec<String>) -> Vec<String> {
    names.sort();
    names.dedup();
    names
}

/// How one conversation goes.
enum Way<'r> {
    /// In parts, as prepared, to these records.
    Parts(Box<Planned>, &'r dyn Records),
    /// Whole, through the legacy restore.
    Whole,
    /// Already there, shown equal by a read-back.
    Present(Unit),
}

fn transfer(
    source: &dyn State,
    target: &dyn State,
    trail: &Trail<'_>,
    records: Option<&dyn Records>,
    (scopes, conversations): (&[String], &[String]),
) -> Result<Vec<Unit>, Error> {
    let all = scopes.is_empty() && conversations.is_empty();
    let scopes = names(if all {
        source.scopes()?
    } else {
        scopes.to_vec()
    });
    let conversations = names(if all {
        source.conversations()?
    } else {
        conversations.to_vec()
    });
    let mut present = Vec::new();
    for scope in &scopes {
        if legacy::scope_occupied(target, scope)? {
            let same = match records {
                Some(_) => legacy::same_scope(source, target, scope)?,
                None => None,
            };
            let unit = same.ok_or_else(|| MemoryError::conflict(scope, None, "target_occupied"))?;
            present.push(unit);
        }
    }
    let mut ways = Vec::new();
    for id in &conversations {
        let way = match records {
            Some(records) if source.last_turn_number(id)? > 0 => {
                let planned = restore::prepare(records, target, source, trail, id)?;
                Way::Parts(Box::new(planned), records)
            }
            _ if restore::occupied(target, id)? => {
                let same = match records {
                    Some(_) => legacy::same_conversation(source, target, trail, id)?,
                    None => None,
                };
                Way::Present(same.ok_or_else(|| Refused::conflict(id, "target_occupied"))?)
            }
            _ => Way::Whole,
        };
        trail.preflight(id)?;
        ways.push((id, way));
    }
    let mut units = Vec::new();
    for scope in &scopes {
        match present.iter().position(|unit| &unit.name == scope) {
            Some(at) => units.push(present.swap_remove(at)),
            None => units.push(legacy::restore_scope(source, target, scope)?),
        }
    }
    for (id, way) in ways {
        units.push(match way {
            Way::Parts(planned, records) => {
                restore::send(records, source, trail, &planned)?;
                planned.unit()
            }
            Way::Whole => legacy::restore_conversation(source, target, trail, id)?,
            Way::Present(unit) => unit,
        });
    }
    Ok(units)
}
