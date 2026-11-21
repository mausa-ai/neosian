//! Client-owned writers: a resolved executable and argv, never a shell.

use std::collections::BTreeMap;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

pub fn which(command: &str, env: &BTreeMap<String, String>) -> Option<PathBuf> {
    let paths = env.get("PATH").map(String::as_str).unwrap_or("");
    if paths.is_empty() && Path::new(command).components().count() == 1 {
        return None;
    }
    let mut candidates: Vec<PathBuf> = if Path::new(command).components().count() > 1 {
        vec![command.into()]
    } else {
        std::env::split_paths(paths)
            .map(|p| p.join(command))
            .collect()
    };
    if cfg!(windows) {
        let extensions = env
            .get("PATHEXT")
            .map(String::as_str)
            .unwrap_or(".COM;.EXE;.BAT;.CMD");
        candidates = candidates
            .into_iter()
            .flat_map(|p| {
                std::iter::once(p.clone()).chain(
                    extensions
                        .split(';')
                        .map(move |ext| PathBuf::from(format!("{}{ext}", p.display()))),
                )
            })
            .collect();
    }
    candidates.into_iter().find(|p| executable(p))
}

fn executable(path: &Path) -> bool {
    if !path.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        path.metadata()
            .is_ok_and(|m| m.permissions().mode() & 0o111 != 0)
    }
    #[cfg(not(unix))]
    {
        true
    }
}

pub fn run(argv: &[String], env: &BTreeMap<String, String>) -> Option<(bool, String)> {
    run_in(argv, env, None, None)
}

/// Run a client CLI with `input` on its standard input (so a value it carries
/// never appears on a command line another process can list), in `cwd` when
/// given (Claude Code's local scope is the directory it runs in).
pub fn run_in(
    argv: &[String],
    env: &BTreeMap<String, String>,
    input: Option<&str>,
    cwd: Option<&Path>,
) -> Option<(bool, String)> {
    let binary = which(&argv[0], env)?;
    let mut command = Command::new(binary);
    command
        .args(&argv[1..])
        .env_clear()
        .envs(env)
        .stdin(if input.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    if let Some(cwd) = cwd {
        command.current_dir(cwd);
    }
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(e) => return Some((false, e.to_string())),
    };
    if let (Some(mut stdin), Some(input)) = (child.stdin.take(), input) {
        let input = input.to_owned();
        // A client that never reads its input must not block this one.
        thread::spawn(move || {
            use std::io::Write;
            let _ = stdin.write_all(input.as_bytes());
        });
    }
    let mut stderr = child.stderr.take().expect("pipe");
    let reader = thread::spawn(move || {
        let mut text = String::new();
        let _ = stderr.read_to_string(&mut text);
        text
    });
    let deadline = Instant::now() + Duration::from_secs(60);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Err(e) => break Err(e.to_string()),
            _ if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                break Err("client command timed out after 60 seconds".into());
            }
            _ => thread::sleep(Duration::from_millis(10)),
        }
    };
    let stderr = reader.join().unwrap_or_default();
    Some(match status {
        Ok(status) => (
            status.success(),
            if stderr.trim().is_empty() {
                format!("exit {}", status.code().unwrap_or(1))
            } else {
                stderr.trim().into()
            },
        ),
        Err(message) => (false, message),
    })
}
