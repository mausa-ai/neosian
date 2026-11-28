//! What a login removes of an import, and when (the L1c slice of the L1
//! plan, with L1e's N2): `login --discard-import`, and a snapshot that no
//! journal names, which every login removes under its lock before anything
//! else.

use std::io::Write;
use std::path::Path;

use serde_json::json;

use super::journal;
use super::link::Link;
use super::session::{self, Miss};
use super::snapshot::{self, DIRECTORY};
use crate::login::Login;
use crate::login::host::Result;
use crate::login::local;
use crate::login::output::{Mark, View, lost, say};
use crate::profile::files;

/// Removes a snapshot that no journal names: the login that took it ended
/// before it was told yes or no, or before it wrote the journal. Under the
/// login lock no other login is between those two steps. True when
/// something was removed.
pub(crate) fn orphan(home: &Path) -> Result<bool> {
    if !home.join(DIRECTORY).exists() || journal::read(home)?.is_some() {
        return Ok(false);
    }
    snapshot::dispose(home).map_err(|f| f.message())
}

/// `login --discard-import`: the host's open session is discarded with the
/// credential it belongs to when that is still this machine's, then the
/// snapshot and journal are removed.
pub(crate) fn discard(args: &Login, out: &mut impl Write, ui: &mut View<impl Write>) -> Result<u8> {
    let home = files::home()?;
    let _op = local::lock(&home)?;
    let waiting = journal::read(&home)?;
    let mut host_said = None;
    if let Some(j) = &waiting
        && let Some(open) = &j.session
        && let Ok(Some(state)) = local::state(&home)
        && state.host == j.host
        && !state.signed_out
        && state.credential_digest == j.credential
        && let Ok(token) = state.token(&home)
    {
        let link = Link::new(&j.host, &token)?;
        host_said = Some(match session::discard(&link, j, &open.id)? {
            Ok(()) => "discarded",
            Err(Miss::Refused(r)) if r.status == 409 => "continuing",
            Err(_) => "unconfirmed",
        });
    }
    let removed = snapshot::dispose(&home).map_err(|f| f.message())?;
    ui.step(&say("step_import")).map_err(lost)?;
    match host_said {
        Some("discarded") => ui.mark(Mark::Ok, &say("discard_host")),
        Some("continuing") => ui.mark(Mark::Warn, &say("discard_continuing")),
        Some(_) => ui.mark(Mark::Warn, &say("discard_unconfirmed")),
        None if waiting.as_ref().is_some_and(|j| j.session.is_some()) => {
            ui.mark(Mark::Warn, &say("discard_unconfirmed"))
        }
        None => Ok(()),
    }
    .and_then(|()| {
        ui.mark(
            Mark::Ok,
            &say(if removed {
                "discard_local"
            } else {
                "discard_none"
            }),
        )
    })
    .map_err(lost)?;
    if args.json {
        let line = json!({"event": "import_discarded", "removed": removed, "host": host_said});
        writeln!(out, "{line}").map_err(lost)?;
    }
    Ok(0)
}
