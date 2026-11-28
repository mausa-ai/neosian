//! Finishing an import the host reported succeeded (the L1d slice of the
//! L1 plan). The journal keeps the host's targets at the stage `succeeded`
//! until this machine has refreshed its assignments and bound its
//! directories (`login/directories.rs`). A run that dies in between is
//! finished by the next login, which sends nothing to the import endpoint.
//!
//! The refresh is login's own (`connect.rs`): the full machine document,
//! never a `304`, with the capability header every machine fetch carries.
//! The imported map and the binding of this directory are installed with
//! the assignments through the recovery journal every login change uses,
//! and the import's journal is removed only after that. Setup is offered
//! after it, never before.

use std::io::{self, Write};
use std::path::{Path, PathBuf};

use serde_json::{Value, json};

use super::consent::Offer;
use super::entry::{offered, resumed};
use super::journal::{self, Journal, Stage};
use super::snapshot;
use crate::login::directories::Finish;
use crate::login::host::Result;
use crate::login::output::{Mark, Style, View, fill, host as shown, lost, say};
use crate::login::{Login, connect};
use crate::profile::files;

/// The import a login finishes, when the journal says the host succeeded.
/// Refused when it belongs to another host than the one this login uses.
pub(crate) fn finishing(home: &Path, url: &str, project: Option<&Path>) -> Result<Option<Finish>> {
    let Some(journal) = journal::read(home)? else {
        return Ok(None);
    };
    if journal.stage != Stage::Succeeded {
        return Ok(None);
    }
    if journal.host != url {
        let host = shown(&journal.host);
        return Err(fill("import_other_host", &[("host", &host)]));
    }
    let scopes = (journal.committed().into_iter())
        .filter(|unit| unit.kind == "scope")
        .map(|unit| unit.name.clone())
        .collect();
    Ok(Some(Finish {
        account: journal.account,
        scopes,
        targets: journal.targets,
        directory: project.map_or_else(|| PathBuf::from("."), Path::to_path_buf),
    }))
}

/// Removes the journal, and the area it lives in, once the directories
/// are bound.
pub(crate) fn finished(home: &Path) -> Result<()> {
    snapshot::dispose(home).map(|_| ()).map_err(|f| f.message())
}

/// A login on a connected machine whose import waits only for its
/// directories: a refresh, whatever was asked, since nothing needs a new
/// approval.
pub(crate) fn waiting(
    args: &Login,
    out: &mut impl Write,
    ui: &mut View<impl Write>,
) -> Result<(Value, u8, Vec<String>)> {
    let refresh = Login {
        refresh: true,
        host: args.host.clone(),
        project: args.project.clone(),
        workspace: args.workspace.clone(),
        capture: args.capture.clone(),
        json: args.json,
        verbose: args.verbose,
        ..Default::default()
    };
    let (doc, _) = connect::run(&refresh, false, out, ui)?;
    let (code, next) = after_connect(args, &doc, ui)?;
    Ok((doc, code, next))
}

/// After a login: what finishing a waiting import did or, on a refresh,
/// that an import is still waiting. The actions it leaves for Next.
pub(crate) fn after_connect(
    args: &Login,
    doc: &Value,
    ui: &mut View<impl Write>,
) -> Result<(u8, Vec<String>)> {
    if doc["import"]["finished"] == true {
        ui.step(&say("step_import")).map_err(lost)?;
        show(ui, &doc["import"]).map_err(lost)?;
        return Ok((0, Vec::new()));
    }
    let Some(waiting) = args.refresh.then(files::home).transpose()? else {
        return Ok((0, Vec::new()));
    };
    let Some(journal) = journal::read(&waiting)? else {
        return Ok((0, Vec::new()));
    };
    let host = shown(&journal.host);
    ui.mark(
        Mark::Warn,
        &fill("refresh_import_waiting", &[("host", &host)]),
    )
    .map_err(lost)?;
    Ok((0, vec![say("next_resume_import")]))
}

/// After a login whose offer was accepted: the import, then, when the host
/// succeeded, the directories.
pub(crate) fn after_login(
    offer: Offer,
    doc: &mut Value,
    args: &Login,
    ui: &mut View<impl Write>,
) -> Result<(u8, Vec<String>)> {
    let (mut answer, mut code, mut next) = offered(offer, args.verbose, ui)?;
    if answer["status"] == "succeeded" {
        let (more, then) = after_success(args, &mut answer, Some(&mut *doc), ui)?;
        code = code.max(more);
        next.extend(then);
    }
    doc["import"] = answer;
    Ok((code, next))
}

/// `login --import` with an import waiting: the same session resumed and,
/// when the host succeeded, the directories.
pub(crate) fn resume(
    args: &Login,
    journal: Journal,
    out: &mut impl Write,
    ui: &mut View<impl Write>,
) -> Result<u8> {
    let (mut answer, mut code, mut next) = resumed(args, journal, ui)?;
    if answer["status"] == "succeeded" {
        let (more, then) = after_success(args, &mut answer, None, ui)?;
        code = code.max(more);
        next.extend(then);
        if more == 0 {
            next.push(say("next_restart_refreshed"));
        }
    }
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

/// The refresh after the host's success, with no login lock held and its
/// own steps unshown; then what it did, under "Import". The refreshed
/// assignments go into `doc` when there is one.
fn after_success(
    args: &Login,
    answer: &mut Value,
    doc: Option<&mut Value>,
    ui: &mut View<impl Write>,
) -> Result<(u8, Vec<String>)> {
    let refresh = Login {
        refresh: true,
        project: args.project.clone(),
        ..Default::default()
    };
    let mut quiet = View::new(io::sink(), Style::PLAIN);
    let refreshed =
        connect::run(&refresh, false, &mut io::sink(), &mut quiet).and_then(|(report, _)| {
            if report["import"]["finished"] == true {
                Ok(report)
            } else {
                Err("the import's journal was not found".to_string())
            }
        });
    let report = match refreshed {
        Ok(report) => report,
        Err(reason) => {
            answer["finished"] = json!(false);
            answer["kept"] = json!(true);
            let line = fill("import_unfinished", &[("reason", &shown(&reason))]);
            ui.mark(Mark::Warn, &line).map_err(lost)?;
            return Ok((1, vec![say("next_finish_import")]));
        }
    };
    for key in ["finished", "directory", "unbound"] {
        answer[key] = report["import"][key].clone();
    }
    if let Some(doc) = doc {
        for key in ["managed", "default_profile"] {
            doc[key] = report[key].clone();
        }
    }
    show(ui, &report["import"]).map_err(lost)?;
    Ok((0, Vec::new()))
}

/// What finishing did: this directory, every target that binds nothing,
/// and how other directories resolve.
fn show(ui: &mut View<impl Write>, summary: &Value) -> io::Result<()> {
    let directory = &summary["directory"];
    let text = |key: &str| directory[key].as_str().map(shown).unwrap_or_default();
    let (root, profile, scope) = (text("root"), text("profile"), text("scope"));
    let default = shown(summary["default"].as_str().unwrap_or_default());
    match directory["bound"].as_str() {
        Some("import") => {
            let words = [
                ("root", root.as_str()),
                ("profile", &profile),
                ("scope", &scope),
            ];
            ui.mark(Mark::Ok, &fill("directory_imported", &words))?;
        }
        Some("kept") => {
            let words = [("root", root.as_str()), ("profile", &profile)];
            ui.mark(Mark::Ok, &fill("directory_kept", &words))?;
        }
        Some(_) => {
            let words = [("root", root.as_str()), ("default", &default)];
            ui.say(&fill("directory_default", &words))?;
        }
        None => {}
    }
    let unbound = summary["unbound"].as_array().map_or(&[][..], Vec::as_slice);
    for left in unbound {
        let named = left["profile"].as_str().map(shown).unwrap_or_default();
        let reason = left["reason"].as_str().unwrap_or("unknown");
        let why = fill(&format!("unbound_{reason}"), &[("profile", &named)]);
        let source = shown(left["source"].as_str().unwrap_or_default());
        let words = [("source", source.as_str()), ("why", &why)];
        ui.mark(Mark::Warn, &fill("directory_unbound", &words))?;
    }
    if summary["mapped"].as_u64().unwrap_or(0) > 0 || !unbound.is_empty() {
        ui.say(&say("directories_later"))?;
    }
    Ok(())
}

/// What logout says about a waiting import, when there is one.
pub(crate) fn logout_note(home: &Path) -> Option<String> {
    let journal = journal::read(home).ok()??;
    let key = match journal.stage {
        Stage::Succeeded => "logout_import_succeeded",
        _ => "logout_import_waiting",
    };
    Some(fill(key, &[("host", &shown(&journal.host))]))
}
