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
use super::policy::{Policy, Rule};

/// The facility, by absolute path: never looked up on PATH.
pub const FACILITY: &str = "/usr/bin/sandbox-exec";
const TOUCH: &str = "/usr/bin/touch";
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

/// Where the probe writes: the first of a name beside the private
/// directory and a name in the home directory that no allowance of the
/// policy covers, wherever the temporary directory lies (X4e P8). A
/// pattern counts as covering everything below its literal directory.
pub fn target(policy: &Policy, private: &Path, home: &Path) -> Option<PathBuf> {
    let mut beside = private.as_os_str().to_os_string();
    beside.push(".probe");
    let named = format!(
        ".neosian-harness-probe-{}-{}",
        std::process::id(),
        uuid::Uuid::new_v4().simple()
    );
    let home = home.canonicalize().ok()?;
    [PathBuf::from(beside), home.join(named)]
        .into_iter()
        .find(|candidate| !policy.allow.iter().any(|rule| covers(rule, candidate)))
}

fn covers(rule: &Rule, path: &Path) -> bool {
    match rule {
        Rule::Literal(literal) => path == Path::new(literal),
        Rule::Subpath(below) => path.starts_with(below),
        Rule::Pattern(pattern) => path.starts_with(literal_dir(pattern)),
    }
}

/// The directory a pattern's literal start names: `^/dev/fd/[0-9]+$` gives
/// `/dev/fd`.
fn literal_dir(pattern: &str) -> String {
    let mut out = String::new();
    let mut chars = pattern.trim_start_matches('^').chars();
    while let Some(c) = chars.next() {
        match c {
            '\\' => out.extend(chars.next()),
            c if "^$.|?*+()[]{}".contains(c) => break,
            c => out.push(c),
        }
    }
    match out.rfind('/') {
        Some(at) => out[..at.max(1)].to_string(),
        None => "/".to_string(),
    }
}

/// Apply `profile` to one write at `target` it must refuse. Applied and
/// refused is the only success; a write that lands is removed and refuses
/// the turn.
pub fn probe(facility: &Path, profile: &str, target: &Path) -> Result<(), String> {
    usable(facility)?;
    let ran = quiet(
        Command::new(facility)
            .arg("-p")
            .arg(profile)
            .arg("--")
            .arg(TOUCH)
            .arg(target),
    );
    if target.symlink_metadata().is_ok() {
        let _ = std::fs::remove_file(target);
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
