//! Beginning one turn (§40.50 items 4, 6, 7, amended by #430): the
//! harness found on an absolute PATH entry, Codex's keys probed, the plan
//! the watcher will check again, the turn number the record compares with,
//! and, for a new session, the check that the announced session did not
//! exist already.

use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use serde_json::json;

use super::adapter::{self, Agent};
use super::link::{self, Naming};
use super::plan::{WireKey, WirePlan};
use super::registry::{Registry, available, shown};
use super::render::Child;
use super::text::say;
use super::turn::{Announce, Turn};
use super::{opencode, probe};
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
    let codex_keys = if child.agent == Agent::Codex {
        probe::codex_keys(&program, &child.directory).map_err(|(key, error)| {
            Outcome::fail(say(
                "codex_probe_failed",
                &[("key", &key), ("error", &error)],
            ))
        })?
    } else {
        Vec::new()
    };
    if child.agent == Agent::OpenCode {
        let carried = std::env::var(opencode::CONFIG_ENV).ok();
        opencode::config(carried.as_deref())
            .map_err(|_| Outcome::fail(say("bad_opencode_config", &[])))?;
    }
    let new_session = uuid::Uuid::new_v4().to_string();
    let wire = WirePlan {
        agent: child.agent.name().to_string(),
        mode: child.mode.name().to_string(),
        directory: shown(&child.directory),
        prompt_bytes: prompt.len(),
        model: child.model.clone(),
        resume: child.session.clone(),
        new_session: Some(new_session.clone()),
        parent: child.parent.clone(),
        codex_keys: codex_keys
            .into_iter()
            .map(|key| WireKey {
                key: key.key.to_string(),
                disabled: key.disabled,
            })
            .collect(),
        store: registry.store.clone(),
    };
    // A new session has no turns; a continued one is read now, so the
    // turns that land during this one can be told apart.
    let before = match &child.session {
        Some(native) => {
            let conversation = naming.conversation(child.agent.name(), native);
            link::last_turn(memory.store.as_ref(), &conversation)
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
    )
    .map_err(|error| spawn_failed(child.agent, &error.to_string()))?;
    turn.before = before;
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
