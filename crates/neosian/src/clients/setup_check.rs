//! Inspect saved configuration and separately exercise the running binary.

use super::{context::Context, diagnostics, probe, status};
use serde_json::{Value, json};
use std::io::{self, Write};

pub fn run(
    context: &Context,
    clients: &[String],
    storage: Value,
    explicit: bool,
    json: bool,
    out: &mut impl Write,
) -> io::Result<u8> {
    let installation = diagnostics::installation(context);
    let clients: Vec<_> = clients
        .iter()
        .map(|c| status::client_status(c, context))
        .collect();
    let mut issues = Vec::new();
    if clients.is_empty() {
        issues.push(
            "No client configuration detected; use setup --client CLIENT to choose one".into(),
        );
    }
    if storage["ready"] != true {
        issues.push(
            "Selected storage configuration is incomplete; inspect storage in this report".into(),
        );
    }
    for client in &clients {
        let name = client["client"].as_str().unwrap();
        let registrations = &client["registrations"];
        for kind in ["mcp", "hooks"] {
            let registration = &registrations[kind];
            let problem = if registration.is_null() {
                Some("registration is missing or unreadable")
            } else if registration["executable"]["resolved"].is_null() {
                Some("executable does not resolve in this shell")
            } else if registration["executable"]["matches_selected"] != true {
                Some("executable differs from the selected binary")
            } else if registration["storage"]["ready"] != true {
                Some("storage configuration is incomplete")
            } else if explicit && registration["storage"] != storage {
                Some("storage differs from the requested selection")
            } else {
                None
            };
            if let Some(problem) = problem {
                issues.push(format!(
                    "{name} {kind}: {problem}; review setup --client {name} before --write"
                ));
            }
        }
        if !registrations["mcp"].is_null()
            && !registrations["hooks"].is_null()
            && registrations["mcp"]["storage"] != registrations["hooks"]["storage"]
        {
            issues.push(format!("{name}: MCP and hooks select different storage"));
        }
        if client["hook_files"].as_array().is_some_and(|f| f.len() > 1) {
            issues.push(format!(
                "{name}: hooks are present in multiple files; review setup --write"
            ));
        }
        if client["mcp_shadowed_by"].is_string() {
            issues.push(format!(
                "{name}: project configuration overrides the user MCP registration"
            ));
        }
    }
    if installation["selected"]["resolved"] != installation["running"] {
        issues.push("The selected executable is not the running binary; run its absolute path with setup --check to test it".into());
    }
    let probe = probe::run();
    if probe["success"] != true {
        issues.push("The isolated MCP/storage probe failed; inspect probe in this report".into());
    }
    let success = issues.is_empty();
    let report = json!({"success":success,"installation":installation,"storage":storage,
        "clients":clients,"probe":probe,"issues":issues,
        "client_connection":"not_observed","hook_delivery":"not_observed"});
    if json {
        writeln!(out, "{report}")?;
    } else {
        render_installation(&report["installation"], out)?;
        render_storage(&report["storage"], out)?;
        for client in report["clients"].as_array().unwrap() {
            writeln!(out, "{}", client["label"].as_str().unwrap())?;
            render_registrations(client, out)?;
        }
        writeln!(
            out,
            "Isolated MCP and file storage: {}",
            if report["probe"]["success"] == true {
                "passed"
            } else {
                "failed"
            }
        )?;
        if let Some(error) = report["probe"]["error"].as_str() {
            writeln!(out, "  {error}")?;
        }
        for issue in report["issues"].as_array().unwrap() {
            writeln!(out, "issue: {}", issue.as_str().unwrap())?;
        }
        writeln!(
            out,
            "Configured storage access is not tested. Client connection and hook delivery are not observed."
        )?;
        writeln!(
            out,
            "Restart the client, review its hook trust settings, and check that neosian-memory connects."
        )?;
    }
    Ok(u8::from(!success))
}

pub fn render_installation(report: &Value, out: &mut impl Write) -> io::Result<()> {
    writeln!(
        out,
        "running   {} ({})",
        report["running"].as_str().unwrap_or("-"),
        report["version"].as_str().unwrap_or("-")
    )?;
    writeln!(
        out,
        "selected  {}",
        report["selected"]["command"].as_str().unwrap_or("-")
    )?;
    writeln!(
        out,
        "PATH      {}",
        report["path_executables"][0]
            .as_str()
            .unwrap_or("no neosian command")
    )?;
    for warning in report["warnings"].as_array().unwrap() {
        writeln!(out, "note      {}", warning.as_str().unwrap())?;
    }
    Ok(())
}

pub fn render_storage(storage: &Value, out: &mut impl Write) -> io::Result<()> {
    writeln!(
        out,
        "storage   {} {}  capture {} (access not checked)",
        storage["kind"].as_str().unwrap_or("unresolved"),
        storage["location"]
            .as_str()
            .or(storage["profile"]["profile"].as_str())
            .unwrap_or(""),
        storage["capture"].as_str().unwrap_or("unknown")
    )?;
    if let Some(error) = storage["error"].as_str() {
        writeln!(out, "  {error}")?;
    }
    if let Some(profile) = storage.get("profile") {
        writeln!(out, "  sources {}", profile["sources"])?;
    }
    if let Some(schema) = storage["schema"].as_str() {
        writeln!(out, "  schema {schema}")?;
    }
    let configuration = storage.get("profile").unwrap_or(storage);
    if let Some(required) = configuration["required_environment"].as_array()
        && !required.is_empty()
    {
        writeln!(
            out,
            "  environment required {}  missing {}",
            configuration["required_environment"], configuration["missing_environment"]
        )?;
    }
    Ok(())
}

pub fn render_registrations(client: &Value, out: &mut impl Write) -> io::Result<()> {
    for kind in ["mcp", "hooks"] {
        let registration = &client["registrations"][kind];
        writeln!(
            out,
            "  {kind:<6}{}",
            registration["executable"]["command"]
                .as_str()
                .unwrap_or("missing")
        )?;
        if !registration.is_null() {
            write!(out, "    ")?;
            render_storage(&registration["storage"], out)?;
        }
    }
    Ok(())
}
