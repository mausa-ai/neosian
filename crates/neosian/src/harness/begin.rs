//! Beginning one turn (§40.50 items 4, 6, 7, amended by #430): the
//! harness found on an absolute PATH entry, its sign-in checked with its
//! own status command outside the sandbox (X4c), Codex's keys probed, the plan
//! the watcher will check again, the turn number the record compares with,
//! and, for a new session, the check that the announced session did not
//! exist already.

use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use serde_json::json;

use super::adapter::{self, Agent};
use super::link::{self, Naming};
use super::plan::WirePlan;
use super::registry::{Registry, available, shown};
use super::render::Child;
use super::signin::{self, Status};
use super::text::say;
use super::turn::{Announce, Turn};
use super::{opencode, probe, status};
use crate::memory::dispatch::{Memory, Outcome};

/// How long `start` and `send` wait for a new session's ID.
const SESSION_WAIT: Duration = Duration::from_secs(3);

fn spawn_failed(agent: Agent, error: &str) -> Outcome {
    Outcome::fail(say(
        "spawn_failed",
        &[("agent", agent.name()), ("error", error)],
    ))
}

/// The check run when a new session is announced: whether its
/// conversation already had turns or a sessions document here.
fn announce(memory: &Memory, naming: &Naming, agent: Agent) -> Announce {
    let store = Arc::clone(&memory.store);
    let scopes: Vec<String> = memory.mounts.iter().map(|m| m.scope.clone()).collect();
    let naming = naming.clone();
    Box::new(move |native: &str| {
        let conversation = naming.conversation(agent.name(), native);
        link::existed(&store, &scopes, &conversation)
    })
}

/// The store flags a read-only child's neosian server is started with
/// (#431): the server's own selection, its backend always named (X4f Q1),
/// and, unless a profile fixes them, every mount of this server, which the
/// watcher makes read-only.
fn store(registry: &Registry, memory: &Memory) -> Vec<String> {
    let mut flags = registry.selection.clone();
    if memory.store.profile().is_some() {
        return flags;
    }
    for mount in &memory.mounts {
        let access = match (mount.read_only, mount.edit_only) {
            (true, _) => ",ro",
            (_, true) => ",eo",
            _ => "",
        };
        flags.extend([
            "--mount".to_string(),
            format!("scope={},path={}{access}", mount.scope, mount.path),
        ]);
    }
    flags
}

/// One headless run of the child's harness with `prompt` on its stdin.
pub fn turn(
    registry: &Registry,
    memory: &Memory,
    naming: &Naming,
    child: &mut Child,
    prompt: &str,
) -> Result<(), Outcome> {
    let path = std::env::var_os("PATH");
    let Some(program) = adapter::which(child.agent.program(), path.as_deref()) else {
        let (found, _) = available();
        let list = if found.is_empty() {
            say("none_available", &[])
        } else {
            found.join(", ")
        };
        return Err(Outcome::fail(say(
            "missing_harness",
            &[("agent", child.agent.name()), ("available", &list)],
        )));
    };
    // The destination this server resolved is still the one on disk (X4f Q1).
    super::selection::drifted(
        &registry.selection,
        registry.identity.as_deref(),
        &child.directory,
    )
    .map_err(Outcome::fail)?;
    let store = store(registry, memory);
    // Admission before any client command runs: on macOS the turn's policy
    // is prepared here, and the client's metadata commands run under it with
    // the child's environment (X4e P4); the watcher prepares its own again.
    let admitted = super::sandbox::admit(child.agent, child.mode, &store, &child.directory)
        .map_err(Outcome::fail)?;
    // One client executable for the status, the metadata and the turn, and
    // none the child could replace (X4f Q5).
    if admitted.launch.writable(&program) {
        let shown = program.display().to_string();
        return Err(Outcome::fail(say(
            "program_writable",
            &[("program", &shown)],
        )));
    }
    // A signed-out client gets the instruction to sign in, never a child;
    // a status neosian cannot read lets the turn start, with a note (X4c).
    // The status command runs outside the policy, which would refuse the
    // sign-in it may need to write, but without the child's credentials.
    let mut notes = admitted.notes.clone();
    match status::check(child.agent, &program, &child.directory, &admitted.launch) {
        Some(Status::SignedOut) => return Err(Outcome::fail(signin::instruction(child.agent))),
        Some(Status::Unknown(reason)) => notes.push(signin::unknown(child.agent, &reason)),
        Some(Status::SignedIn) | None => {}
    }
    if child.agent == Agent::Codex {
        // Checked here so `start` refuses; the watcher checks again.
        probe::codex(&admitted.launch, &program, &child.directory, child.mode)
            .map_err(Outcome::fail)?;
    }
    if child.agent == Agent::OpenCode {
        let carried = std::env::var(opencode::CONFIG_ENV).ok();
        opencode::config(carried.as_deref())
            .map_err(|_| Outcome::fail(say("bad_opencode_config", &[])))?;
    }
    drop(admitted);
    let new_session = uuid::Uuid::new_v4().to_string();
    let nonce = uuid::Uuid::new_v4().simple().to_string();
    let wire = WirePlan {
        agent: child.agent.name().to_string(),
        mode: child.mode.name().to_string(),
        directory: shown(&child.directory),
        prompt_bytes: prompt.len(),
        model: child.model.clone(),
        resume: child.session.clone(),
        new_session: Some(new_session.clone()),
        parent: child.parent.clone(),
        store,
        nonce: Some(nonce.clone()),
        identity: registry.identity.clone(),
        home: super::selection::home().map(|home| home.display().to_string()),
        program: Some(program.display().to_string()),
    };
    // A new session has no turns; a continued one is read now, so the
    // turns that land during this one can be told apart.
    let before = match &child.session {
        Some(native) => {
            let conversation = naming.conversation(child.agent.name(), native);
            link::last_turn_bounded(&memory.store, &conversation)
        }
        None => Ok(0),
    };
    let check = child
        .session
        .is_none()
        .then(|| announce(memory, naming, child.agent));
    let number = child.turns + 1;
    let limit = Duration::from_secs(registry.settings.turn_seconds);
    let mut turn = Turn::begin(
        child.agent,
        &json!(wire),
        prompt,
        &child.directory,
        limit,
        check,
        nonce,
    )
    .map_err(|error| spawn_failed(child.agent, &error.to_string()))?;
    turn.before = before;
    turn.notes = notes;
    if child.session.is_none() && child.agent == Agent::ClaudeCode {
        child.session = Some(new_session);
    }
    child.turns = number;
    child.current = Some(turn);
    let deadline = Instant::now() + SESSION_WAIT;
    while child.agent != Agent::ClaudeCode
        && child.session.is_none()
        && Instant::now() < deadline
        && child.running()
    {
        child.learn();
        thread::sleep(Duration::from_millis(20));
    }
    Ok(())
}
