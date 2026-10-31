//! The documented statusline payload, reduced to one bounded local observation.
//! This never opens a memory backend or initiates a model/network request.

use super::{cache_shell, files};
use crate::store::fileio;
use serde_json::{Value, json};
use std::fs;
use std::io::{self, Read, Write};
use std::path::Path;
use std::process::Stdio;
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

fn identifier(value: &Value) -> Option<&str> {
    value.as_str().filter(|s| {
        !s.is_empty()
            && s.len() <= 128
            && s.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    })
}

fn atom(value: &Value) -> Option<&str> {
    value.as_str().filter(|s| {
        !s.is_empty()
            && s.len() <= 128
            && s.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
    })
}

fn persist(directory: &Path, payload: &Value, cache: &Value, now: u64) -> io::Result<()> {
    let id = identifier(&payload["session_id"])
        .ok_or_else(|| io::Error::other("missing session identifier"))?;
    let mut observation = cache.clone();
    observation["format"] = json!(1);
    observation["session_id"] = json!(id);
    observation["version"] = json!(atom(&payload["version"]));
    observation["model"] = json!(atom(&payload["model"]["id"]));
    observation["observed_at_ms"] = json!(now);
    observation["subscription"] = json!(["five_hour", "seven_day"].iter().any(|key| {
        payload["rate_limits"][key]["used_percentage"]
            .as_f64()
            .is_some_and(|n| n.is_finite() && (0.0..=100.0).contains(&n))
    }));
    let path = directory.join("observations").join(format!("{id}.json"));
    fileio::atomic_write(&path, &serde_json::to_string(&observation)?, false)
}

/// Execute only the user's previously configured statusline, with its original
/// stdin and environment. Its stdout stays byte-for-byte intact; its process is
/// bounded independently of the observation, which has already been persisted.
fn delegate(command: &str, input: &[u8], out: &mut impl Write) -> io::Result<()> {
    let mut child = cache_shell::delegate(command)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()?;
    let mut stdin = child.stdin.take().expect("pipe");
    let owned = input.to_vec();
    let writer = thread::spawn(move || {
        let _ = stdin.write_all(&owned);
    });
    let stdout = child.stdout.take().expect("pipe");
    let (send, receive) = mpsc::channel();
    let _reader = thread::spawn(move || {
        let mut bytes = Vec::new();
        let _ = stdout.take(65536).read_to_end(&mut bytes);
        let _ = send.send(bytes);
    });
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if child.try_wait()?.is_some() {
            break;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            break;
        }
        thread::sleep(Duration::from_millis(5));
    }
    // The bounded statusline generally finishes in milliseconds. A delegated
    // command may leave descendants holding pipes: don't wait for their threads.
    if writer.is_finished() {
        let _ = writer.join();
    }
    if let Ok(bytes) = receive.recv_timeout(Duration::from_millis(100)) {
        out.write_all(&bytes)?;
    }
    Ok(())
}

pub fn run(
    directory: &Path,
    payload: &Value,
    cache: &Value,
    input: &[u8],
    now: u64,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<u8> {
    if persist(directory, payload, cache, now).is_err() {
        writeln!(err, "neosian cache observation unavailable")?;
    }
    let saved = files::read(&directory.join("statusline.json"));
    if let Some(command) = saved
        .as_ref()
        .and_then(|v| v["original"]["command"].as_str())
        && delegate(command, input, out).is_err()
    {
        writeln!(err, "neosian: previous statusline could not run")?;
    }
    // If the mod did not load (for example after an incompatible host update),
    // its missing/stale heartbeat is visible even though its own UI cannot run.
    if let Some(id) = identifier(&payload["session_id"]) {
        let receipt = directory
            .join("observations")
            .join(format!("{id}.refresh.json"));
        let current = fs::metadata(&receipt).is_ok_and(|m| m.len() <= 8192)
            && files::read(&receipt).is_some_and(|v| {
                v["session_id"] == id
                    && v["observed_at_ms"].as_u64().is_some_and(|t| {
                        t <= now.saturating_add(2000) && now.saturating_sub(t) <= 90000
                    })
            });
        if !current {
            writeln!(out, "\nNeosian · keep-warm not observed; check /plugin")?;
        }
    }
    Ok(0)
}
