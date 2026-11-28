//! The offer to bring local memory along (the L1c slice of the L1 plan,
//! the owner's ruling on consent). It is made after discovery and before
//! the device start, only when the host names an `import_endpoint`, the
//! home holds memory and the login is a person's: in a terminal it asks
//! once, default no; `--import` answers yes. A refresh, an environment
//! credential, `--non-interactive`, `--json` without `--import` and any
//! login whose stdin is not a terminal never ask and never import.
//!
//! The person sees the offered totals, that imported history carries tool
//! arguments and output exactly as stored and may hold secrets, and that
//! approving the machine and importing are separate choices. An offer that
//! never reaches Begin leaves nothing behind: its snapshot is disposed of
//! when the offer is dropped, unless the journal took it over.

use std::io::{self, IsTerminal, Write};
use std::path::{Path, PathBuf};

use serde_json::{Value, json};

use super::copy::holds_store;
use super::journal::{Left, Unit};
use super::report::{self, Offered};
use super::snapshot::{self, Snapshot};
use super::units::inventory;
use crate::login::Login;
use crate::login::host::{Discovery, Result};
use crate::login::local::{self, State};
use crate::login::output::{Mark, View, fill, host as shown, lost, say};

/// An accepted offer: the snapshot and what it holds.
pub(crate) struct Offer {
    pub home: PathBuf,
    pub host: String,
    pub endpoint: String,
    /// The account the offer was made for, when the machine was connected.
    pub account: Option<String>,
    pub snapshot: Snapshot,
    pub units: Vec<Unit>,
    pub local: Vec<Left>,
    /// The journal holds the snapshot now; dropping the offer keeps it.
    pub armed: bool,
}

impl Offer {
    /// The counts the device start carries for the approval page.
    pub(crate) fn intent(&self) -> Value {
        let offered = Offered::of(&self.units);
        json!({"scopes": offered.scopes, "conversations": offered.conversations, "bytes": offered.bytes})
    }
}

impl Drop for Offer {
    fn drop(&mut self) {
        if !self.armed {
            let _ = snapshot::dispose(&self.home);
        }
    }
}

/// Makes the offer when this login may; None when it may not, when the
/// home holds nothing to import, or when the person declines.
pub(crate) fn offer(
    args: &Login,
    prior: Option<&State>,
    discovery: &Discovery,
    url: &str,
    home: &Path,
    ui: &mut View<impl Write>,
) -> Result<Option<Offer>> {
    if args.refresh || args.non_interactive || local::environment().is_some() {
        return Ok(None);
    }
    let Some(endpoint) = discovery.import_endpoint.clone() else {
        if args.import {
            let host = shown(&discovery.name);
            return Err(fill("import_unoffered", &[("host", &host)]));
        }
        return Ok(None);
    };
    let ask = !args.import && !args.json && io::stdin().is_terminal();
    if !(args.import || ask) || !holds_store(home) {
        if args.import {
            ui.step(&say("step_memory")).map_err(lost)?;
            ui.mark(Mark::Ok, &say("memory_empty")).map_err(lost)?;
        }
        return Ok(None);
    }
    ui.step(&say("step_memory"))
        .and_then(|()| ui.say(&say("memory_measuring")))
        .and_then(|()| ui.flush())
        .map_err(lost)?;
    // A snapshot no journal names never reached a host: take a fresh one.
    if super::journal::read(home)?.is_some() {
        return Err(fill("import_waiting", &[("host", &shown(url))]));
    }
    snapshot::dispose(home).map_err(|f| f.message())?;
    let taken = snapshot::take(home).map_err(|f| f.message())?;
    let mut offer = Offer {
        home: home.to_path_buf(),
        host: url.to_string(),
        endpoint,
        account: prior.map(|s| s.account.id.clone()),
        snapshot: taken,
        units: Vec::new(),
        local: Vec::new(),
        armed: false,
    };
    let made = inventory(&offer.snapshot, &mut |_, _, _| Ok(())).map_err(|e| e.text())?;
    offer.units = made
        .units
        .iter()
        .map(|u| Unit {
            kind: u.kind.into(),
            name: u.name.clone(),
            sha256: u.sha256.clone(),
            bytes: u.bytes,
        })
        .collect();
    offer.local = made
        .omitted
        .iter()
        .map(|o| Left {
            kind: o.kind.into(),
            name: o.name.clone(),
            reason: o.reason.into(),
        })
        .collect();
    if offer.units.is_empty() {
        ui.mark(Mark::Ok, &say("memory_empty")).map_err(lost)?;
        return Ok(None);
    }
    report::offer(ui, &offer.units, &offer.local).map_err(lost)?;
    if !ask {
        ui.mark(Mark::Ok, &say("memory_chosen")).map_err(lost)?;
        return Ok(Some(offer));
    }
    let question = fill("memory_question", &[("host", &shown(&discovery.name))]);
    ui.prompt(&question).map_err(lost)?;
    let mut answer = String::new();
    io::stdin()
        .read_line(&mut answer)
        .map_err(|_| "cannot read the import answer")?;
    if matches!(answer.trim().to_ascii_lowercase().as_str(), "y" | "yes") {
        return Ok(Some(offer));
    }
    ui.mark(Mark::Ok, &say("memory_declined")).map_err(lost)?;
    Ok(None)
}
