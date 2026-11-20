//! After a verified install, the old release starts the new release's registration
//! refresh (§40.48). This command line is an interface between releases: an older
//! binary calls a newer one, so its words never change and nothing is ever required.

use crate::clients::context::shell_join;
use serde_json::{Value, json};
use std::{
    io::{self, Read},
    process::{Command, Stdio},
    sync::mpsc,
    thread,
};

/// What runs on the installed executable; a JSON update appends `--json`.
pub const ARGS: [&str; 2] = ["setup", "--refresh"];
const HINT: &str = "run neosian setup --refresh";

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Stream {
    Out,
    Err,
}

/// The refresh's exit code (0 or 1) and, for JSON, its report. Output passes to
/// `emit` as it arrives, except a JSON report, which is collected. There is no
/// kill timer: stopping a refresh while it writes is worse than waiting for it.
pub fn refresh(
    executable: &str,
    json: bool,
    emit: &mut dyn FnMut(Stream, &[u8]) -> io::Result<()>,
) -> io::Result<(u8, Value)> {
    let mut argv: Vec<String> = std::iter::once(executable)
        .chain(ARGS)
        .map(str::to_owned)
        .collect();
    if json {
        argv.push("--json".into());
    }
    let started = format!("Refreshing existing registrations: {}\n", shell_join(&argv));
    emit(Stream::Err, started.as_bytes())?;
    let spawned = Command::new(executable)
        .args(&argv[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn();
    let mut child = match spawned {
        Ok(child) => child,
        Err(error) => return incomplete(&format!("it did not start: {error}"), json, emit),
    };
    let (sender, receiver) = mpsc::channel();
    let pipes: [(Stream, Option<Box<dyn Read + Send>>); 2] = [
        (Stream::Out, child.stdout.take().map(|p| Box::new(p) as _)),
        (Stream::Err, child.stderr.take().map(|p| Box::new(p) as _)),
    ];
    let readers: Vec<_> = pipes
        .into_iter()
        .filter_map(|(stream, pipe)| pipe.map(|pipe| (stream, pipe)))
        .map(|(stream, mut pipe)| {
            let sender = sender.clone();
            thread::spawn(move || {
                let mut buffer = [0; 8192];
                while let Ok(count @ 1..) = pipe.read(&mut buffer) {
                    if sender.send((stream, buffer[..count].to_vec())).is_err() {
                        break;
                    }
                }
            })
        })
        .collect();
    drop(sender);
    let (mut report, mut failed) = (Vec::new(), None);
    for (stream, chunk) in receiver {
        if json && stream == Stream::Out {
            report.extend(chunk);
        } else if failed.is_none()
            && let Err(error) = emit(stream, &chunk)
        {
            // Keep draining so the refresh can finish; report the error after it.
            failed = Some(error);
        }
    }
    for reader in readers {
        let _ = reader.join();
    }
    let code = child.wait()?.code();
    if let Some(error) = failed {
        return Err(error);
    }
    let exit = code.map_or("it ended without an exit code".into(), |c| {
        format!("exit {c}")
    });
    if !json {
        return match code {
            Some(c @ (0 | 1)) => Ok((c as u8, Value::Null)),
            _ => incomplete(&exit, json, emit),
        };
    }
    let report = serde_json::from_slice::<Value>(&report)
        .ok()
        .filter(|r| r["success"].is_boolean());
    match (code, report) {
        (Some(0), Some(report)) if report["success"] == true => Ok((0, report)),
        (Some(1), Some(report)) if report["success"] == false => Ok((1, report)),
        (Some(0 | 1), _) => incomplete("its report was not one refresh object", json, emit),
        _ => incomplete(&exit, json, emit),
    }
}

fn incomplete(
    why: &str,
    json: bool,
    emit: &mut dyn FnMut(Stream, &[u8]) -> io::Result<()>,
) -> io::Result<(u8, Value)> {
    let error = format!(
        "the installed release did not complete the registration refresh ({why}); it stays installed"
    );
    emit(
        Stream::Err,
        format!("error: {error}\nhint: {HINT}\n").as_bytes(),
    )?;
    Ok((
        1,
        if json {
            json!({"success": false, "error": error, "hint": HINT})
        } else {
            Value::Null
        },
    ))
}
