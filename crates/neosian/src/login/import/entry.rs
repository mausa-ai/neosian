//! The ways into an import at login (the L1c slice of the L1 plan): a login
//! whose offer was accepted, `login --import` when an import is waiting
//! (it resumes with the credential the session belongs to, never a new
//! approval), and `login --discard-import`. A waiting import blocks every
//! other login except a refresh, because a new approval would replace the
//! credential its session is bound to.
//!
//! Nothing is sent before three checks pass: the host is the one the
//! journal names, the account and the credential are the ones the import
//! began with, and (on a resume) the host still answers that credential
//! with that account. A mismatch stops with the snapshot and journal kept
//! and says how to discard them.

use std::io::Write;
use std::path::Path;

use serde_json::{Value, json};

use super::consent::Offer;
use super::drive::{Outcome, Run, drive};
use super::journal::{self, Journal, Stage, Unit};
use super::link::Link;
use super::report::{self, Offered};
use super::session::{self, Miss};
use super::snapshot::{self, Snapshot};
use crate::login::Login;
use crate::login::host::{self, Result};
use crate::login::local::{self, State};
use crate::login::output::{Mark, View, fill, host as shown, lost, say};
use crate::profile::files;
use crate::store::time::Stamp;

/// Which way this login goes.
pub(crate) enum Route {
    Login,
    Resume(Box<Journal>),
    Discard,
}

pub(crate) fn route(args: &Login) -> Result<Route> {
    if args.import && local::environment().is_some() {
        return Err(say("import_environment"));
    }
    if args.discard_import {
        return Ok(Route::Discard);
    }
    match journal::read(&files::home()?)? {
        Some(waiting) if args.import => Ok(Route::Resume(Box::new(waiting))),
        Some(waiting) if !args.refresh => {
            Err(fill("import_waiting", &[("host", &shown(&waiting.host))]))
        }
        _ => Ok(Route::Login),
    }
}

/// The source scope that is the person's own memory: the current login's
/// `user:` scope, when the snapshot has it.
fn personal(units: &[Unit]) -> Option<String> {
    let scope = crate::home::user_mount().ok()?.scope;
    units
        .iter()
        .any(|u| u.kind == "scope" && u.name == scope)
        .then_some(scope)
}

/// After a login that installed the machine and accepted the offer.
pub(crate) fn after_login(
    mut offer: Offer,
    doc: &mut Value,
    verbose: bool,
    ui: &mut View<impl Write>,
) -> Result<(u8, Vec<String>)> {
    let home = offer.home.clone();
    let _op = local::lock(&home)?;
    let state = local::state(&home)?.ok_or("the machine login is missing")?;
    if state.host != offer.host
        || offer
            .account
            .as_ref()
            .is_some_and(|a| *a != state.account.id)
    {
        return Err(say("import_other_account"));
    }
    let token = state.token(&home)?;
    let journal = Journal {
        journal_format: 1,
        host: offer.host.clone(),
        import_endpoint: offer.endpoint.clone(),
        account: state.account.id.clone(),
        credential: state.credential_digest.clone(),
        snapshot: offer.snapshot.id.clone(),
        begun_at: Stamp::now().iso(),
        personal: personal(&offer.units),
        units: offer.units.clone(),
        local: offer.local.clone(),
        session: None,
        omitted: Vec::new(),
        refused: Vec::new(),
        stage: Stage::Begin,
    };
    // Journalled before Begin: a rerun finds the session's credential and snapshot.
    journal::write(&home, &journal)?;
    offer.armed = true;
    let offered = Offered::of(&journal.units);
    let words = [
        (
            "units",
            report::count(offered.scopes + offered.conversations, "unit"),
        ),
        ("size", report::size(offered.bytes)),
        ("host", shown(&state.discovery.name)),
    ];
    let pairs: Vec<(&str, &str)> = words.iter().map(|(k, v)| (*k, v.as_str())).collect();
    ui.step(&say("step_import"))
        .and_then(|()| ui.say(&fill("import_sending", &pairs)))
        .map_err(lost)?;
    let (answer, code, next) = session_run(
        &home,
        &state,
        &token,
        journal,
        Some(&offer.snapshot),
        verbose,
        ui,
    )?;
    doc["import"] = answer;
    Ok((code, next))
}

fn session_run(
    home: &Path,
    state: &State,
    token: &str,
    mut journal: Journal,
    snapshot: Option<&Snapshot>,
    verbose: bool,
    ui: &mut View<impl Write>,
) -> Result<(Value, u8, Vec<String>)> {
    let link = Link::new(&state.host, token)?;
    let outcome = {
        let mut run = Run {
            link: &link,
            home,
            origin: &state.host,
            ui,
            verbose,
        };
        drive(&mut run, &mut journal, snapshot)
    };
    let mut next = report::outcome(ui, &outcome, &journal).map_err(lost)?;
    if outcome.terminal() {
        snapshot::dispose(home).map_err(|f| f.message())?;
    } else if journal::write(home, &journal).is_err() {
        next.push(say("next_resume_import"));
    }
    let code = u8::from(matches!(outcome, Outcome::Stopped(_) | Outcome::Failed(..)));
    Ok((report::json(&outcome, &journal), code, next))
}

/// `login --import` with an import waiting: the same session, resumed.
pub(crate) fn resume(
    args: &Login,
    journal: Journal,
    out: &mut impl Write,
    ui: &mut View<impl Write>,
) -> Result<u8> {
    let home = files::home()?;
    let _op = local::lock(&home)?;
    // Nothing goes to a host the journal does not name.
    if args.host.as_deref().is_some_and(|h| h != journal.host) {
        return Err(fill(
            "import_other_host",
            &[("host", &shown(&journal.host))],
        ));
    }
    let state = local::state(&home)?.ok_or_else(|| say("import_other_credential"))?;
    if state.host != journal.host {
        return Err(fill(
            "import_other_host",
            &[("host", &shown(&journal.host))],
        ));
    }
    if state.account.id != journal.account {
        return Err(say("import_other_account"));
    }
    if state.signed_out || state.credential_digest != journal.credential {
        return Err(say("import_other_credential"));
    }
    let token = state
        .token(&home)
        .map_err(|_| say("import_other_credential"))?;
    // The host itself names the account behind the credential before any
    // import request is made.
    let agent = host::agent(15)?;
    let machine = &state.discovery.machine_endpoint;
    let fetched = host::fetch(&agent, &state.host, machine, &token, None)
        .map_err(|e| fill("import_unreachable", &[("reason", &e)]))?;
    match fetched {
        Some((doc, _)) if doc.account.id == journal.account => {}
        _ => return Err(say("import_other_account")),
    }
    let opened = snapshot::open(&home).map_err(|f| f.message())?;
    let snapshot = opened.filter(|s| s.id == journal.snapshot);
    if snapshot.is_none() && matches!(journal.stage, Stage::Begin | Stage::Transfer) {
        return Err(say("import_copy_gone"));
    }
    let at = shown(&journal.begun_at);
    ui.step(&say("step_import"))
        .and_then(|()| {
            ui.say(&fill(
                "import_resuming",
                &[("host", &shown(&state.discovery.name)), ("at", &at)],
            ))
        })
        .map_err(lost)?;
    let (answer, code, next) = session_run(
        &home,
        &state,
        &token,
        journal,
        snapshot.as_ref(),
        args.verbose,
        ui,
    )?;
    if args.json {
        let mut line = json!({"event": "import"});
        line["import"] = answer;
        writeln!(out, "{line}").map_err(lost)?;
    }
    if !next.is_empty() {
        ui.step(&say("step_next"))
            .and_then(|()| ui.list(&next))
            .map_err(lost)?;
    }
    Ok(code)
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
