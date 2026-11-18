//! Installation-local capture controls for the mapped, workspace-prefixed ID.

use super::generation;
use super::payload::{PROMPT_EVENT, Payload};
use super::spool::Spool;
use crate::store::fileio::atomic_write;
use std::fs;
use std::io::{self, Write};
use std::path::Path;

fn session_key(id: &str) -> Option<&str> {
    let (workspace, key) = id.strip_prefix("w-")?.split_once('-')?;
    let hex = |s: &str| {
        s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    };
    (workspace.len() == 32 && key.len() == 64 && hex(workspace) && hex(key)).then_some(key)
}

pub fn paused(dir: &Path, id: &str) -> io::Result<bool> {
    dir.join(format!("{id}.paused")).try_exists()
}

// The JSONL belongs to the full ID. The original policy guard and lock span
// all workspaces using this installation, client and native session. Retain
// the guard as long as another workspace has any pending data under it.
fn discard(dir: &Path, spool: &Spool, id: &str, key: &str) -> io::Result<()> {
    generation::retire(dir, id, key)?;
    spool.clear(id)?;
    finish(dir, id)?;
    let guard = dir.join(format!("{key}.policy"));
    if guard.try_exists()? {
        let suffix = format!("-{key}.jsonl");
        for entry in fs::read_dir(dir)? {
            if entry?
                .file_name()
                .to_str()
                .is_some_and(|s| s.ends_with(&suffix))
            {
                return Ok(());
            }
        }
        fs::remove_file(guard)?;
    }
    Ok(())
}

pub fn finish(dir: &Path, id: &str) -> io::Result<()> {
    match fs::remove_file(dir.join(format!("{id}.turn"))) {
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(()),
        other => other,
    }
}

/// Called under the same lock as the landing. After resume, late tool and
/// completion events can still carry content from the interrupted turn.
pub fn blocks(
    dir: &Path,
    id: &str,
    key: &str,
    payload: &Payload,
    agent: &str,
    enabled: bool,
) -> io::Result<bool> {
    let tagged = matches!(agent, "cursor" | "opencode");
    if paused(dir, id)? {
        if tagged {
            generation::ignore(dir, key, payload)?;
        }
        // Also finish a pause interrupted after its marker was published.
        discard(dir, &Spool::new(dir), id, key)?;
        return Ok(true);
    }
    if !enabled {
        return Ok(false);
    }
    let event = payload
        .get("hook_event_name")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("");
    let resuming = dir.join(format!("{id}.resuming"));
    let resumed = resuming.try_exists()?;
    let switched = dir.join(format!("{key}.capture-fence")).try_exists()?;
    if tagged && generation::blocks(dir, id, key, event, payload, resumed || switched)? {
        return Ok(true);
    }
    if resumed {
        if event != PROMPT_EVENT {
            return Ok(true);
        }
        fs::remove_file(resuming)?;
    }
    if switched {
        // The workspace is resolved on every hook. An assistant-only callback
        // can belong to the paused workspace even when today's selection differs.
        let turn = dir.join(format!("{id}.turn"));
        if event == PROMPT_EVENT {
            atomic_write(&turn, "", true)?;
        } else if !turn.try_exists()? && !Spool::new(dir).file(id).try_exists()? {
            return Ok(true);
        }
    }
    Ok(false)
}

fn set(dir: &Path, id: &str, key: &str, pause: bool) -> io::Result<&'static str> {
    let spool = Spool::new(dir);
    let _held = spool.lock(key)?;
    let marker = dir.join(format!("{id}.paused"));
    let resuming = dir.join(format!("{id}.resuming"));
    let state = if pause {
        atomic_write(&dir.join(format!("{key}.capture-fence")), "", true)?;
        // Publish the stop first. An interrupted cleanup remains paused, and
        // either a retry or resume finishes cleanup before admitting new data.
        atomic_write(&marker, "", true)?;
        discard(dir, &spool, id, key)?;
        "capture_paused"
    } else if marker.try_exists()? {
        discard(dir, &spool, id, key)?;
        fs::rename(marker, resuming)?;
        "capture_resumes_on_prompt"
    } else if resuming.try_exists()? {
        "capture_resumes_on_prompt"
    } else {
        "capture_follows_profile"
    };
    #[cfg(unix)]
    {
        fs::File::open(dir)?.sync_all()?;
        if let Some(home) = dir.parent() {
            fs::File::open(home)?.sync_all()?;
        }
    }
    Ok(state)
}

pub fn run(
    args: &super::cli::Record,
    control: &crate::clients::install::Conversation,
    pause: bool,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<u8> {
    let id = &control.conversation_id;
    let json = args.json || control.json;
    if args.profile.is_some()
        || args.root.is_some()
        || args.url.is_some()
        || args.schema.is_some()
        || args.scope.is_some()
        || !args.mount.is_empty()
        || args.project.is_some()
        || args.spool.is_some()
        || args.agent != super::cli::DEFAULT_AGENT
    {
        return crate::cli::usage(
            json,
            "pause and resume use the complete conversation ID and local neosian home; omit recording options",
            out,
            err,
        );
    }
    let Some(key) = session_key(id) else {
        return crate::cli::usage(
            json,
            "use the full profile conversation ID from the session identity line",
            out,
            err,
        );
    };
    let dir = crate::home::home()?.join("profile-spool");
    match set(&dir, id, key, pause) {
        Ok(disposition) => {
            if json {
                writeln!(
                    out,
                    "{}",
                    serde_json::json!({"conversation_id":id,"disposition":disposition})
                )?;
            } else {
                let message = match disposition {
                    "capture_paused" => "Capture paused",
                    "capture_resumes_on_prompt" => {
                        "Profile capture resumes on the next user prompt"
                    }
                    _ => "Capture follows the profile policy",
                };
                writeln!(out, "{message} for {id} on this installation.")?;
            }
            Ok(0)
        }
        Err(error) => crate::conversation::failed(&error.to_string(), None, json, out, err),
    }
}
