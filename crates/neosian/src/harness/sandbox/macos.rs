//! The macOS application: the operating system's sandbox profile language
//! applied at launch by `/usr/bin/sandbox-exec`, which Apple marks
//! deprecated and still ships (verified on macOS 27.2.1). It applies the
//! profile to itself and then replaces itself with the harness, so the
//! harness keeps the process ID the watcher started and every descendant
//! inherits the profile, while the watcher stays outside it. It exits 71
//! without running anything when a profile cannot be applied, for example
//! from a process that is already sandboxed. A missing executable refuses
//! the turn; nothing falls back to an unconfined child.

use std::io;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

use super::super::events::head;
use super::super::text::say;

/// The facility, by absolute path: never looked up on PATH.
pub const FACILITY: &str = "/usr/bin/sandbox-exec";
const TOUCH: &str = "/usr/bin/touch";
const TRUE: &str = "/usr/bin/true";
const ERROR_BYTES: usize = 512;

/// Whether the facility is an executable file.
pub fn usable(facility: &Path) -> Result<(), String> {
    if super::super::adapter::executable(facility) {
        return Ok(());
    }
    Err(say(
        "sandbox_missing",
        &[("facility", &facility.display().to_string())],
    ))
}

fn quiet(command: &mut Command) -> io::Result<Output> {
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
}

fn refused(error: &str) -> String {
    say(
        "sandbox_refused",
        &[("error", head(error.trim(), ERROR_BYTES))],
    )
}

/// Whether this process can apply a profile at all (the server's check).
/// The profile is this process's own: macOS lets a sandboxed process apply
/// a profile identical to the one it runs under, and no other.
pub fn can_apply(facility: &Path) -> Result<(), String> {
    usable(facility)?;
    let profile = format!(
        "(version 1)(allow default)(deny file-write* (literal \"/neosian-probe-{}\"))",
        std::process::id()
    );
    let ran = quiet(Command::new(facility).arg("-p").arg(&profile).arg(TRUE));
    match ran {
        Ok(output) if output.status.success() => Ok(()),
        Ok(output) => Err(refused(&String::from_utf8_lossy(&output.stderr))),
        Err(error) => Err(refused(&error.to_string())),
    }
}

/// A path beside the private directory, outside every allowance.
fn target(private: &Path) -> PathBuf {
    let mut name = private.as_os_str().to_os_string();
    name.push(".probe");
    PathBuf::from(name)
}

/// Apply `profile` to one write it must refuse, beside the private
/// directory. Applied and refused is the only success; a write that lands
/// is removed and refuses the turn.
pub fn probe(facility: &Path, profile: &str, private: &Path) -> Result<(), String> {
    usable(facility)?;
    let target = target(private);
    let ran = quiet(
        Command::new(facility)
            .arg("-p")
            .arg(profile)
            .arg("--")
            .arg(TOUCH)
            .arg(&target),
    );
    if target.symlink_metadata().is_ok() {
        let _ = std::fs::remove_file(&target);
        return Err(say("sandbox_unenforced", &[]));
    }
    match ran {
        // touch's own failure: the profile applied and refused the write.
        Ok(output) if output.status.code() == Some(1) => Ok(()),
        Ok(output) => Err(refused(&String::from_utf8_lossy(&output.stderr))),
        Err(error) => Err(refused(&error.to_string())),
    }
}

/// The command that starts `program` under `profile`.
pub fn command(facility: &Path, profile: &str, program: &Path) -> Command {
    let mut command = Command::new(facility);
    command.arg("-p").arg(profile).arg("--").arg(program);
    command
}
