//! Standalone `setup`'s report of a run: JSON, or the full text with each
//! client's paths, commands and notes (login shows it under `--verbose`).

use super::{
    context::CLIENTS,
    files, setup_check,
    setup_outcome::{Outcome, Ran},
};
use serde_json::{Value, json};
use std::io::{self, Write};

pub fn discovery_text(rows: &[Value]) -> String {
    rows.iter()
        .filter(|r| r["installed"] == false)
        .map(|r| {
            format!(
                "  {}: not found; searched {}\n",
                text(r, "client"),
                text(r, "directory")
            )
        })
        .collect()
}

fn text<'a>(value: &'a Value, key: &str) -> &'a str {
    value[key].as_str().unwrap_or("-")
}

fn line(kind: &str, envelope: &Value, write: bool) -> String {
    let prefix = format!("  {kind:<6}{}  ", text(envelope, "config_path"));
    let result = if envelope["success"] != true {
        format!("refused: {}", text(envelope, "error"))
    } else if let Some(apply) = envelope["apply"].as_str() {
        if !write {
            format!("would run: {apply}")
        } else if envelope["applied"] == true {
            format!("applied: {apply}")
        } else {
            let why = envelope["apply_error"]
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| format!("{} is not on PATH", shlex::split(apply).unwrap()[0]));
            format!("not applied ({why}); run: {apply}")
        }
    } else if let Some(action) = envelope["action"]
        .as_str()
        .filter(|_| !write || envelope["applied"] == true)
    {
        format!("{}{}", if write { "applied: " } else { "would " }, action)
    } else if write {
        if envelope["applied"] != true {
            "not applied (preflight failed)".into()
        } else if envelope["created"] == true {
            "created".into()
        } else {
            "updated".into()
        }
    } else {
        "would write".into()
    };
    prefix + &result
}

/// Standalone setup's answer for a run that `ran`; its exit code.
pub fn report(
    ran: Ran,
    json_output: bool,
    url: bool,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<u8> {
    let outcome = match ran {
        Ran::Exit(code) => return Ok(code),
        Ran::NoClient(searched) => {
            let message = format!(
                "no client found: none of {} is installed",
                CLIENTS.join(", ")
            );
            if json_output {
                writeln!(
                    out,
                    "{}",
                    json!({"error": message, "hint": null, "searched": searched})
                )?;
            }
            writeln!(err, "error: {message}")?;
            write!(err, "{}", discovery_text(&searched))?;
            return Ok(1);
        }
        Ran::Cancelled => {
            writeln!(err, "setup cancelled; nothing written")?;
            return Ok(1);
        }
        Ran::NoneSelected => {
            writeln!(out, "No clients selected; nothing written.")?;
            return Ok(0);
        }
        Ran::Done(outcome) => outcome,
    };
    if json_output {
        writeln!(
            out,
            "{}",
            json!({"written": outcome.write, "level": outcome.level, "clients": outcome.rows,
                "searched": outcome.searched, "installation":outcome.installation,"storage":outcome.storage})
        )?;
    } else {
        full(&outcome, out, err)?;
    }
    if url {
        writeln!(
            err,
            "hint: set NEOSIAN_CLIENT_TOKEN in each client's own environment: the token is never written into a registration or a hook line"
        )?;
    }
    Ok(u8::from(outcome.failed))
}

/// Every client's paths, commands and notes, then the clients not found.
pub fn full(outcome: &Outcome, out: &mut impl Write, err: &mut impl Write) -> io::Result<()> {
    let write = outcome.write;
    setup_check::render_installation(&outcome.installation, out)?;
    setup_check::render_storage(&outcome.storage, out)?;
    for row in &outcome.rows {
        writeln!(out, "{}", text(row, "label"))?;
        if row["profile"].is_object() {
            writeln!(
                out,
                "  profile {}  capture {}  context {}",
                text(&row["profile"], "profile"),
                text(&row["profile"], "capture"),
                text(&row["profile_support"], "context")
            )?;
            writeln!(
                out,
                "  capture fields {}  history source {}  support {}",
                row["profile"]["definition"]["capture"]["fields"],
                text(&row["profile"]["definition"]["history"], "source"),
                text(&row["profile_support"], "capture")
            )?;
        }
        for kind in ["mcp", "hooks"] {
            writeln!(out, "{}", line(kind, &row[kind], write))?;
        }
        if let Some(skills) = row.get("skills") {
            writeln!(out, "{}", line("skills", skills, write))?;
        }
        if let Some(cache) = row.get("cache") {
            writeln!(out, "{}", line("cache", cache, write))?;
            if let Some(note) = cache["note"].as_str() {
                writeln!(out, "  note  {note}")?;
            }
            if !write && let Some(commands) = cache["commands"].as_array() {
                for argv in commands {
                    let argv: Vec<_> = argv
                        .as_array()
                        .unwrap()
                        .iter()
                        .filter_map(Value::as_str)
                        .map(str::to_owned)
                        .collect();
                    writeln!(out, "    {}", super::context::shell_join(&argv))?;
                }
            }
        }
        for note in [&row["mcp"]["note"], &row["hooks"]["trust_hint"]]
            .into_iter()
            .filter_map(Value::as_str)
        {
            writeln!(out, "  note  {note}")?;
        }
        if let Some(shadow) = row["mcp"]["mcp_shadowed_by"].as_str() {
            writeln!(
                err,
                "note: preserved shared {shadow}; it overrides Muse's user registration"
            )?;
        }
        if let Some(files) = row["hooks"].get("files")
            && !write
        {
            write!(out, "{}", files::pretty(files))?;
        }
    }
    if !write {
        writeln!(err, "hint: re-run with --write to apply")?;
    }
    if write && !outcome.failed {
        writeln!(
            out,
            "Registrations saved. Run this executable with setup --check, then restart the client and review hook trust."
        )?;
    }
    write!(out, "{}", discovery_text(&outcome.searched))
}
