//! After a verified install, the old release starts the new release's registration
//! refresh (§40.48). This command line is an interface between releases: an older
//! binary calls a newer one, so its words never change and nothing is ever required.

use serde_json::{Value, json};
use std::{
    io::{self, Write},
    process::{Command, Stdio},
};

/// What runs on the installed executable; a JSON update appends `--json`.
pub const ARGS: [&str; 2] = ["setup", "--refresh"];
const HINT: &str = "run neosian setup --write to refresh the registrations";

/// The refresh's exit code (0 or 1) and, for JSON, its report.
pub fn refresh(
    executable: &str,
    json: bool,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<(u8, Value)> {
    let mut command = Command::new(executable);
    command.args(ARGS).stdin(Stdio::null());
    if json {
        command.arg("--json");
    }
    let result = match command.output() {
        Ok(result) => result,
        Err(error) => return incomplete(&format!("it did not start: {error}"), json, err),
    };
    let code = result.status.code();
    let exit = code.map_or("it ended without an exit code".into(), |c| {
        format!("exit {c}")
    });
    if !json {
        out.write_all(&result.stdout)?;
        err.write_all(&result.stderr)?;
        return match code {
            Some(c @ (0 | 1)) => Ok((c as u8, Value::Null)),
            _ => incomplete(&exit, json, err),
        };
    }
    err.write_all(&result.stderr)?;
    let report = serde_json::from_slice::<Value>(&result.stdout)
        .ok()
        .filter(|r| r["success"].is_boolean());
    match (code, report) {
        (Some(0), Some(report)) if report["success"] == true => Ok((0, report)),
        (Some(1), Some(report)) if report["success"] == false => Ok((1, report)),
        (Some(0 | 1), _) => incomplete("its report was not one refresh object", json, err),
        _ => incomplete(&exit, json, err),
    }
}

fn incomplete(why: &str, json: bool, err: &mut impl Write) -> io::Result<(u8, Value)> {
    let error = format!(
        "the installed release did not complete the registration refresh ({why}); it stays installed"
    );
    writeln!(err, "error: {error}")?;
    writeln!(err, "hint: {HINT}")?;
    Ok((
        1,
        if json {
            json!({"success": false, "error": error, "hint": HINT})
        } else {
            Value::Null
        },
    ))
}
