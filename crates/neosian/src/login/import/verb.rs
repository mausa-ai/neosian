//! `neosian login-snapshot`, a hidden diagnostic verb (the L1b slice of the
//! L1 plan): the contracts' door to the login import's snapshot of the home
//! `NEOSIAN_HOME` names. It sends nothing and `neosian login` does not use
//! it. `take` takes a snapshot and measures its units, `units` opens the
//! sealed snapshot again, checked against its manifest, and measures them
//! again, and `dispose` removes it. One JSON object is printed. With
//! `--bodies DIR` every body is written to the new directory DIR as
//! `UUUU-PPPP.json`, the unit's index and the part's index from 0, with
//! exactly the bytes the import would send.

use std::io::{self, Write};
use std::path::PathBuf;

use clap::{Args, ValueEnum};
use serde_json::{Value, json};

use super::snapshot::{self, Fault, Snapshot};
use super::units::{Inventory, inventory};
use crate::conversation::Error;
use crate::store::fileio::{private, private_mkdir};
use crate::store::time::Stamp;

#[derive(Args)]
pub struct LoginSnapshot {
    /// What to do with the snapshot.
    #[arg(value_enum)]
    action: Action,
    /// Write every unit's bodies to this new directory.
    #[arg(long, value_name = "DIR")]
    bodies: Option<PathBuf>,
}

#[derive(Clone, Copy, ValueEnum)]
enum Action {
    Take,
    Units,
    Dispose,
}

fn measured(snapshot: &Snapshot, bodies: Option<&PathBuf>) -> Result<Inventory, Fault> {
    if let Some(directory) = bodies {
        if directory.exists() {
            return Err(Fault::Store(Error::Value(format!(
                "--bodies: {} exists",
                directory.display()
            ))));
        }
        private_mkdir(directory)?;
    }
    let mut write = |unit: usize, part: usize, body: &[u8]| -> Result<(), Error> {
        if let Some(directory) = bodies {
            let name = directory.join(format!("{unit:04}-{part:04}.json"));
            private()
                .write(true)
                .create_new(true)
                .open(name)?
                .write_all(body)?;
        }
        Ok(())
    };
    Ok(inventory(snapshot, &mut write)?)
}

fn answer(snapshot: &Snapshot, made: &Inventory) -> Value {
    let units: Vec<Value> = made
        .units
        .iter()
        .map(|u| {
            json!({
                "kind": u.kind, "name": u.name, "sha256": u.sha256, "bytes": u.bytes,
                "documents": u.documents, "versions": u.versions,
                "redactions": u.redactions, "turns": u.turns,
                "projections": u.projections, "parts": u.parts,
            })
        })
        .collect();
    let omitted: Vec<Value> = made
        .omitted
        .iter()
        .map(|o| {
            json!({
                "kind": o.kind, "name": o.name, "reason": o.reason,
                "projections": o.projections, "redactions": o.redactions,
            })
        })
        .collect();
    let (scopes, conversations, bytes) = made.totals();
    json!({
        "snapshot": snapshot::describe(snapshot, Stamp::now()),
        "units": units,
        "omitted": omitted,
        "totals": {"scopes": scopes, "conversations": conversations, "bytes": bytes},
    })
}

pub fn run(args: &LoginSnapshot, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let home = crate::home::home()?;
    let done = match args.action {
        Action::Take => snapshot::take(&home)
            .and_then(|taken| Ok(answer(&taken, &measured(&taken, args.bodies.as_ref())?))),
        Action::Units => match snapshot::open(&home) {
            Ok(Some(opened)) => {
                measured(&opened, args.bodies.as_ref()).map(|made| answer(&opened, &made))
            }
            Ok(None) => Err(Fault::Store(Error::Value(
                "no login import snapshot is taken".to_string(),
            ))),
            Err(fault) => Err(fault),
        },
        Action::Dispose => snapshot::dispose(&home).map(|disposed| json!({"disposed": disposed})),
    };
    match done {
        Ok(value) => {
            writeln!(out, "{value}")?;
            Ok(0)
        }
        Err(fault) => {
            writeln!(err, "error: {}", fault.message())?;
            Ok(1)
        }
    }
}
