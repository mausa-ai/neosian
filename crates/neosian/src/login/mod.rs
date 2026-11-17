//! The machine connection. The host owns accounts and grants; the CLI holds
//! only a credential and portable profile selections for this installation.
mod apply;
mod connect;
pub use connect::refresh_if_due;
mod device;
mod host;
pub(crate) mod local;
mod project;
mod redo;
mod retire;
#[cfg(windows)]
mod windows;
#[cfg(windows)]
mod windows_acl;

use crate::profile::files;
use clap::Args;
use host::Result;
use serde_json::{Value, json};
use std::{
    io::{self, IsTerminal, Write},
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
}

#[derive(Args)]
pub struct Logout {
    /// Remove managed profiles and their project bindings from this machine.
    #[arg(long)]
    forget: bool,
    #[arg(long)]
    json: bool,
}

pub fn run(args: &Login, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    match connect::run(args, false, out, err) {
        Ok(doc) if args.json => writeln!(out, "{doc}").map(|()| 0),
        Ok(doc) => {
            writeln!(
                out,
                "Connected as {}. Managed profiles: {}.",
                doc["account"]["email"].as_str().unwrap_or(""),
                doc["managed"]
            )?;
            if let Some(removed) = doc["removed"].as_array().filter(|rows| !rows.is_empty()) {
                writeln!(out, "Removed assignments: {}", json!(removed))?;
            }
            writeln!(
                out,
                "Run neosian setup --write to register your agents, then restart them."
            )?;
            if !args.refresh && !args.non_interactive && io::stdin().is_terminal() {
                write!(err, "Run setup now? [y/N] ")?;
                err.flush()?;
                let mut response = String::new();
                io::stdin().read_line(&mut response)?;
                if matches!(response.trim().to_ascii_lowercase().as_str(), "y" | "yes") {
                    let status = std::process::Command::new(std::env::current_exe()?)
                        .args(["setup", "--write"])
                        .status()?;
                    return Ok(u8::from(!status.success()));
                }
            }
            Ok(0)
        }
        Err(message) => failure(&message, args.json, out, err),
    }
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
    retire::drain(&home, err)?;
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
    let pending = home
        .join("profile-spool")
        .read_dir()
        .ok()
        .is_some_and(|mut files| {
            files.any(|entry| {
                entry.is_ok_and(|e| e.path().extension().is_some_and(|x| x == "policy"))
            })
        });
    if pending {
        writeln!(err, "An open turn remains in the private spool.")
            .map_err(|_| "cannot display pending capture warning")?;
    }
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
