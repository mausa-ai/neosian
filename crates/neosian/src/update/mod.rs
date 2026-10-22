//! Binary updates use published GitHub Releases and cargo-dist's sibling updater (§39.5).

mod check;
mod plan;
mod receipt;
#[cfg(test)]
mod tests;
mod version;

use crate::{clients::context::Context, config, home, shape, store::time::Stamp};
use check::{RELEASE_URL, cached, latest, stamp};
use clap::Args;
use plan::plan;
use serde_json::json;
use std::{
    io::{self, Write},
    path::Path,
    process::{Command, Stdio},
};

const MODES: [&str; 3] = ["off", "notify", "auto"];

#[derive(Args)]
pub struct Update {
    /// Check and print without applying (the default).
    #[arg(long)]
    check: bool,
    /// Apply through the updater that belongs to this installation.
    #[arg(long)]
    write: bool,
    #[arg(long)]
    json: bool,
    /// Set the human-door update policy and stop.
    #[arg(long, value_parser = MODES)]
    mode: Option<String>,
}

fn mode(root: &Path) -> String {
    config::read(&root.join("config.toml"))
        .ok()
        .and_then(|doc| doc.get("update")?.get("mode")?.as_str().map(str::to_string))
        .filter(|m| MODES.contains(&m.as_str()))
        .unwrap_or("off".into())
}

fn apply(argv: &[String], err: &mut impl Write) -> io::Result<u8> {
    // Pin the checked tag: a newly published release cannot cross a fence.
    let result = Command::new(&argv[0])
        .args(&argv[1..])
        .stdin(Stdio::null())
        .output()?;
    err.write_all(&result.stdout)?;
    err.write_all(&result.stderr)?;
    Ok(result
        .status
        .code()
        .and_then(|c| c.try_into().ok())
        .unwrap_or(1))
}

fn install(decision: &plan::Decision, context: &Context, err: &mut impl Write) -> io::Result<u8> {
    match apply_and_verify(decision, context, err) {
        Err(error) if error.kind() != io::ErrorKind::BrokenPipe => {
            writeln!(err, "error: {error}")?;
            Ok(1)
        }
        result => result,
    }
}

fn apply_and_verify(
    decision: &plan::Decision,
    context: &Context,
    err: &mut impl Write,
) -> io::Result<u8> {
    let code = apply(&decision.argv, err)?;
    if code != 0 {
        return Ok(code);
    }
    let result = Command::new(&context.executable).arg("version").output()?;
    let expected = format!("neosian v{}", decision.latest.as_deref().unwrap());
    if !result.status.success() || String::from_utf8_lossy(&result.stdout).trim() != expected {
        writeln!(
            err,
            "error: updater exited successfully but the executable does not report the checked release"
        )?;
        return Ok(1);
    }
    Ok(0)
}

pub fn run(args: &Update, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    match execute(args, out, err) {
        Err(error) if error.kind() != io::ErrorKind::BrokenPipe => {
            if args.json {
                writeln!(out, "{}", json!({"error": error.to_string(), "hint": null}))?;
            }
            writeln!(err, "error: {error}")?;
            Ok(1)
        }
        result => result,
    }
}

fn execute(args: &Update, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let root = home::home()?;
    if let Some(mode) = &args.mode {
        let path = root.join("config.toml");
        let mut doc = config::read(&path)?;
        let section = doc
            .entry("update")
            .or_insert_with(|| toml::Value::Table(toml::Table::new()));
        if !section.is_table() {
            *section = toml::Value::Table(toml::Table::new());
        }
        section
            .as_table_mut()
            .unwrap()
            .insert("mode".into(), toml::Value::String(mode.clone()));
        config::write(&path, &doc)?;
        if args.json {
            writeln!(out, "{}", json!({"mode": mode}))?;
        } else {
            writeln!(out, "update mode {mode}")?;
        }
        return Ok(0);
    }
    let context = Context::new()?;
    let mode = mode(&root);
    let latest = latest(RELEASE_URL);
    if let Some(latest) = &latest {
        stamp(&root, latest, Stamp::now())?;
    }
    let decision = plan(
        crate::version::VERSION,
        latest.as_ref(),
        &context,
        args.write || (mode == "auto" && !args.check),
    );
    if args.json {
        writeln!(
            out,
            "{}",
            json!({"current": crate::version::VERSION, "latest": decision.latest, "shape": shape::kind(&context.env), "mode": mode, "action": decision.action, "line": decision.line})
        )?;
    } else {
        writeln!(out, "{}", decision.line)?;
    }
    out.flush()?;
    if decision.action == "apply" {
        install(&decision, &context, err)
    } else {
        Ok(u8::from(matches!(
            decision.action,
            "refuse" | "unreachable"
        )))
    }
}

pub fn human_door(verb: &str, terminal: bool, json: bool) -> bool {
    terminal && !json && matches!(verb, "status" | "configure")
}

pub fn human_check(err: &mut impl Write) -> io::Result<Option<u8>> {
    let root = home::home()?;
    let mode = mode(&root);
    if mode == "off" {
        return Ok(None);
    }
    let now = Stamp::now();
    let latest = cached(&root, now).or_else(|| {
        let version = latest(RELEASE_URL)?;
        let _ = stamp(&root, &version, now);
        Some(version)
    });
    let Some(latest) = latest else {
        return Ok(None);
    };
    let context = Context::new()?;
    let decision = plan(
        crate::version::VERSION,
        Some(&latest),
        &context,
        mode == "auto",
    );
    if decision.action != "current" {
        writeln!(err, "{}", decision.line)?;
    }
    if decision.action == "apply" {
        let code = install(&decision, &context, err)?;
        if code != 0 {
            return Ok(Some(code));
        }
        let status = Command::new(context.executable)
            .args(std::env::args_os().skip(1))
            .status()?;
        return Ok(Some(
            status.code().and_then(|c| c.try_into().ok()).unwrap_or(1),
        ));
    }
    Ok(None)
}
