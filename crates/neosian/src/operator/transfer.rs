//! Archives are FileStore roots; every selected target is preflighted
//! before restoring the first unit (DESIGN §26).

use std::io::{self, Write};
use std::path::{Component, PathBuf};

use clap::Args;
use serde_json::{Value, json};

use super::{Flags, count, finish};
use crate::cli::usage;
use crate::conversation::ids::check_conversation_id;
use crate::conversation::{Error, Refused};
use crate::state::State;
use crate::store::error::MemoryError;
use crate::store::grammar::check_scope;
use crate::store::{FileStore, Restored};

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
        let store = selection.open()?;
        let archive = FileStore::open(&directory)?;
        let (source, target): (&dyn State, &dyn State) = if import {
            (&archive, store.as_ref())
        } else {
            (store.as_ref(), &archive)
        };
        let units = transfer(source, target, &args.scopes, &args.conversations)?;
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

fn transfer(
    source: &dyn State,
    target: &dyn State,
    scopes: &[String],
    conversations: &[String],
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
    for scope in &scopes {
        if !target.list_documents(scope, "")?.is_empty()
            || !target.history(scope, None, Some(1))?.is_empty()
            || !target.redactions(scope, None, Some(1))?.is_empty()
        {
            return Err(MemoryError::conflict(scope, None, "target_occupied").into());
        }
    }
    for id in &conversations {
        if target.last_turn_number(id)? != 0 || !target.read_projections(id, 0, Some(1))?.is_empty()
        {
            return Err(Refused::conflict(id, "target_occupied").into());
        }
    }
    let mut units = Vec::new();
    for scope in scopes {
        let mut documents = Vec::new();
        for entry in source.list_documents(&scope, "")? {
            if let Some(document) = source.read(&scope, &entry.path)? {
                documents.push(Restored {
                    path: entry.path,
                    content: document.content,
                    envelope: document.envelope,
                });
            }
        }
        let mut versions = source.history(&scope, None, None)?;
        versions.sort_by(|a, b| (&a.path, a.version).cmp(&(&b.path, b.version)));
        let mut redactions = source.redactions(&scope, None, None)?;
        redactions.reverse();
        let unit = Unit {
            kind: "scope",
            name: scope.clone(),
            documents: documents.len(),
            versions: versions.len(),
            redactions: redactions.len(),
            ..Unit::default()
        };
        target.restore_scope(&scope, &documents, versions, &redactions)?;
        units.push(unit);
    }
    for id in conversations {
        let mut redactions =
            match source.turn_redactions(Some(std::slice::from_ref(&id)), None, 500) {
                Ok(acts) => acts,
                Err(Error::Memory(error)) if error.code == "agent_configuration_error" => {
                    Vec::new()
                }
                Err(error) => return Err(error),
            };
        redactions.reverse();
        let turns = source.read_turns(&id, 0, None)?;
        let projections = source.read_projections(&id, 0, None)?;
        target.restore_conversation(&id, &turns, &projections, &redactions)?;
        units.push(Unit {
            kind: "conversation",
            name: id,
            turns: turns.len(),
            projections: projections.len(),
            redactions: redactions.len(),
            ..Unit::default()
        });
    }
    Ok(units)
}
