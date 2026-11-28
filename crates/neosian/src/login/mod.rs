//! The machine connection. The host owns accounts and grants; the CLI holds
//! only a credential and portable profile selections for this installation.
mod apply;
mod conditional;
mod connect;
pub use connect::refresh_if_due;
mod device;
mod host;
pub(crate) mod import;
pub(crate) mod local;
mod output;
mod project;
mod redo;
mod retire;
mod steps;
mod terminal;
mod unblock;
#[cfg(windows)]
mod windows;
#[cfg(windows)]
mod windows_acl;

use crate::profile::files;
use clap::Args;
use host::Result;
use output::{Style, View};
use serde_json::{Value, json};
use std::{
    io::{self, Write},
    path::PathBuf,
};

#[derive(Args, Default)]
pub struct Login {
    /// HTTPS discovery document. Defaults to neosian cloud on the first login.
    #[arg(long)]
    host: Option<String>,
    /// Directory to bind to an assigned workspace.
    #[arg(long)]
    project: Option<PathBuf>,
    /// Assigned profile name, new to create one, or none to skip checkout binding.
    #[arg(long)]
    workspace: Option<String>,
    /// Local recording preference. Off leaves memory and skills available.
    #[arg(long, value_parser=["on","off"])]
    capture: Option<String>,
    /// Refresh existing assignments with the validated machine credential.
    #[arg(long)]
    refresh: bool,
    /// Read NEOSIAN_LOGIN_TOKEN from the environment and skip the browser.
    #[arg(long)]
    non_interactive: bool,
    /// Emit the public device code and final outcome as newline-delimited JSON.
    #[arg(long)]
    json: bool,
    /// Also show identifiers and each agent's paths, commands and notes in text mode.
    #[arg(long)]
    verbose: bool,
    /// Bring this machine's memory to the host after the browser approval,
    /// without asking; resumes an import that is waiting.
    #[arg(long, conflicts_with_all = ["refresh", "non_interactive"])]
    import: bool,
    /// Discard a waiting import: the host's open session and the local copy.
    #[arg(long, conflicts_with_all = ["import", "refresh", "non_interactive", "workspace", "project", "capture"])]
    discard_import: bool,
}

#[derive(Args)]
pub struct Logout {
    /// Remove managed profiles and their project bindings from this machine.
    #[arg(long)]
    forget: bool,
    #[arg(long)]
    json: bool,
}

/// Login in text mode is a conversation on stderr: named steps, prompts and
/// the host's notice. With `--json`, stdout carries the JSON lines alone and
/// stderr the same steps without colour.
pub fn run(args: &Login, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let style = if args.json {
        Style::PLAIN
    } else {
        Style::stderr()
    };
    let mut ui = View::new(err, style);
    let done = match import::route(args) {
        Ok(import::Route::Login) => {
            connect::run(args, false, out, &mut ui).map(|(mut doc, offer)| {
                let (code, next) = match offer {
                    None => (0, Vec::new()),
                    Some(offer) => import::after_login(offer, &mut doc, args.verbose, &mut ui)
                        .unwrap_or_else(|message| {
                            doc["import"] = json!({"status":"stopped","stopped":message});
                            let _ = ui.mark(output::Mark::Fail, &message);
                            (1, Vec::new())
                        }),
                };
                (doc, code, next)
            })
        }
        Ok(import::Route::Resume(waiting)) => {
            return finished(
                args,
                import::resume(args, *waiting, out, &mut ui),
                out,
                &mut ui,
            );
        }
        Ok(import::Route::Discard) => {
            return finished(args, import::discard(args, out, &mut ui), out, &mut ui);
        }
        Err(message) => Err(message),
    };
    match done {
        Ok((doc, code, _)) if args.json => writeln!(out, "{doc}").map(|()| code),
        Ok((doc, code, next)) => released(&doc, out)
            .and_then(|()| steps::finish(args, &doc, &mut ui, next))
            .map(|done| done.max(code)),
        Err(message) if args.json => failure(&message, true, out, &mut io::sink()),
        Err(message) => ui.fail(&message).map(|()| 1),
    }
}

/// The end of an import-only run: its code, or its failure as login's.
fn finished(
    args: &Login,
    done: Result<u8>,
    out: &mut impl Write,
    ui: &mut View<impl Write>,
) -> io::Result<u8> {
    match done {
        Ok(code) => Ok(code),
        Err(message) if args.json => failure(&message, true, out, &mut io::sink()),
        Err(message) => ui.fail(&message).map(|()| 1),
    }
}
/// Standard output in text mode, byte for byte as released: scripts may read
/// it. The steps on stderr carry the same facts for a person.
fn released(doc: &Value, out: &mut impl Write) -> io::Result<()> {
    writeln!(
        out,
        "Connected as {}. Managed profiles: {}.",
        output::host(doc["account"]["email"].as_str().unwrap_or("")),
        doc["managed"]
    )?;
    if let Some(removed) = doc["removed"].as_array().filter(|rows| !rows.is_empty()) {
        writeln!(out, "Removed assignments: {}", json!(removed))?;
    }
    writeln!(
        out,
        "Run neosian setup --write to register your agents, then restart them."
    )
}

fn failure(
    message: &str,
    json_output: bool,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<u8> {
    if json_output {
        writeln!(out, "{}", json!({"error":"login","hint":message}))?;
    } else {
        writeln!(err, "{message}")?;
    }
    Ok(1)
}

fn disconnect(args: &Logout, err: &mut impl Write) -> Result<Value> {
    let home = files::home()?;
    let _op = local::lock(&home)?;
    apply::recover(&home)?;
    retire::drain(&home, |warning| writeln!(err, "{warning}"))?;
    let mut s = local::state(&home)?.ok_or("no managed login in this home")?;
    let bearer = if !s.signed_out && s.credential_source == local::Source::File {
        s.stored(&home).map(|c| c.token)
    } else {
        s.token(&home)
    };
    let revoked = bearer
        .and_then(|token| {
            let agent = host::agent(5)?;
            host::revoke(&agent, &s.discovery.revocation_endpoint, &token)
        })
        .is_ok();
    if !revoked {
        writeln!(
            err,
            "Remote revocation was unconfirmed. Revoke this machine in the host's devices page."
        )
        .map_err(|_| "cannot display revocation result")?;
    }
    if s.credential_source == local::Source::Environment {
        writeln!(err, "Unset NEOSIAN_LOGIN_TOKEN in the process environment.")
            .map_err(|_| "cannot display logout guidance")?;
    }
    let spool = home.join("profile-spool");
    let open = spool.read_dir().ok().is_some_and(|mut files| {
        files.any(|entry| entry.is_ok_and(|e| e.path().extension().is_some_and(|x| x == "policy")))
    });
    // Closed turns that have not landed wait in the same spool (#447).
    let closed = crate::record::waiting(&spool);
    if open {
        writeln!(err, "An open turn remains in the private spool.")
            .map_err(|_| "cannot display pending capture warning")?;
    }
    if closed {
        writeln!(
            err,
            "Closed turns that have not landed remain in the private spool."
        )
        .map_err(|_| "cannot display pending capture warning")?;
    }
    let pending = open || closed;
    s.signed_out = true;
    local::write(&home, local::STATE, &s)?;
    local::remove(&home, local::CREDENTIAL)?;
    if args.forget {
        apply::forget(&home, &s)?;
        writeln!(
            err,
            "Unbound directories now use local storage and record sessions locally."
        )
        .map_err(|_| "cannot display local recording warning")?;
    }
    Ok(
        json!({"event":"logged_out","revoked":revoked,"forgotten":args.forget,"pending_capture":pending}),
    )
}

pub fn logout(args: &Logout, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    match disconnect(args, err) {
        Ok(doc) if args.json => writeln!(out, "{doc}").map(|()| 0),
        Ok(_) => writeln!(out, "Logged out.").map(|()| 0),
        Err(message) => failure(&message, args.json, out, err),
    }
}
