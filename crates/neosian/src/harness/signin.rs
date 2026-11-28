//! A child's sign-in (X4c). A child cannot sign in or renew a sign-in for
//! itself: on macOS neosian's policy allows no credential file and no
//! keychain write (X4b), and no client's sign-in flow can run headless. So
//! before a turn the watcher's server runs the client's own read-only
//! status command outside the sandbox (`status.rs`), and what it answers is
//! read here; a sign-in failure the client reports in its own structured
//! error during a turn becomes the same instruction.
//!
//! Only an answer that plainly says the client is not signed in counts as
//! signed out. A timeout, a command that cannot run, an error and anything
//! not recognised are unknown: the turn starts, with a note that says so.
//! Nothing a status command prints is shown except, for an unknown answer,
//! its first line with every word that may be a credential or a personal
//! detail hidden.

use serde_json::Value;

use super::adapter::Agent;
use super::events::{Event, head};
use super::text::say;

/// The client's own status command, or None where the client has none
/// that answers for a run: OpenCode's `opencode auth list` lists stored
/// credentials, but a free model needs none and an environment variable
/// counts, so its answer cannot say that a run would be signed out.
pub fn command(agent: Agent) -> Option<&'static [&'static str]> {
    match agent {
        // Claude Code 2.1.332: JSON with `loggedIn`, exit 1 when false.
        Agent::ClaudeCode => Some(&["auth", "status"]),
        // codex-cli 0.172.0: one line on stderr, exit 1 when not logged in.
        Agent::Codex => Some(&["login", "status"]),
        Agent::OpenCode => None,
    }
}

/// The status command as the person would type it.
pub fn typed(agent: Agent) -> String {
    let words = command(agent).unwrap_or_default();
    std::iter::once(agent.program())
        .chain(words.iter().copied())
        .collect::<Vec<_>>()
        .join(" ")
}

/// What a status command's answer said.
#[derive(Debug, PartialEq, Eq)]
pub enum Status {
    SignedIn,
    SignedOut,
    /// Not known; why, in a sentence safe to show.
    Unknown(String),
}

/// The client's answer read: the exit code (None for a signal) and what it
/// printed on each stream, already bounded by the caller.
pub fn classify(agent: Agent, code: Option<i32>, stdout: &str, stderr: &str) -> Status {
    let failed = code.is_some_and(|code| code != 0);
    match agent {
        Agent::ClaudeCode => {
            let logged_in = serde_json::from_str::<Value>(stdout.trim())
                .ok()
                .and_then(|answer| answer.get("loggedIn").and_then(Value::as_bool));
            match logged_in {
                Some(true) if code == Some(0) => Status::SignedIn,
                Some(false) if failed => Status::SignedOut,
                _ => unrecognized(code, stdout, stderr),
            }
        }
        Agent::Codex => {
            let lines = || stderr.lines().chain(stdout.lines()).map(str::trim);
            let signed_in = lines().any(|line| {
                line.starts_with("Logged in using") || line.starts_with("API key configured")
            });
            if code == Some(0) && signed_in {
                Status::SignedIn
            } else if failed && lines().any(|line| line == "Not logged in") {
                Status::SignedOut
            } else {
                unrecognized(code, stdout, stderr)
            }
        }
        Agent::OpenCode => unrecognized(code, stdout, stderr),
    }
}

fn unrecognized(code: Option<i32>, stdout: &str, stderr: &str) -> Status {
    let status = code.map_or_else(|| "a signal".to_string(), |code| code.to_string());
    let shown = scrub(if stdout.trim().is_empty() {
        stderr
    } else {
        stdout
    });
    Status::Unknown(if shown.is_empty() {
        say("signin_silent", &[("status", &status)])
    } else {
        say(
            "signin_unrecognized",
            &[("status", &status), ("answer", &shown)],
        )
    })
}

/// The most of a status answer's line an unknown result shows.
const SHOWN_BYTES: usize = 160;

/// The first non-empty line of `answer`, bounded, control characters
/// dropped, and every word that may be a credential, a key's masked form,
/// an e-mail address or another personal detail replaced by `[hidden]`.
pub fn scrub(answer: &str) -> String {
    let line = answer
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or_default();
    let printable: String = line.chars().filter(|c| !c.is_control()).collect();
    let words: Vec<&str> = printable
        .split_whitespace()
        .map(|word| if hidden(word) { "[hidden]" } else { word })
        .collect();
    head(&words.join(" "), SHOWN_BYTES).to_string()
}

/// A word that may be secret or personal: long, or letters mixed with
/// digits (keys, tokens, IDs), or marked as a key's masked form (`*`), an
/// assignment (`=`) or an address (`@`).
fn hidden(word: &str) -> bool {
    let digits = word.chars().any(|c| c.is_ascii_digit());
    let letters = word.chars().any(|c| c.is_alphabetic());
    word.chars().count() >= 20
        || (digits && letters && word.len() >= 8)
        || word.contains(['*', '=', '@'])
        || word.starts_with("sk-")
}

/// The one instruction a signed-out client gets, before a turn or during
/// one: how to sign in, outside the child.
pub fn instruction(agent: Agent) -> String {
    let key = match agent {
        Agent::ClaudeCode => "signin_claude_code",
        Agent::Codex => "signin_codex",
        Agent::OpenCode => "signin_opencode",
    };
    say(key, &[])
}

/// The turn failed because the client's sign-in failed.
pub fn failed(agent: Agent) -> Event {
    Event::SignIn(instruction(agent))
}

/// The note for a status neosian could not read; the turn starts anyway.
pub fn unknown(agent: Agent, reason: &str) -> String {
    say(
        "signin_unknown",
        &[
            ("agent", agent.name()),
            ("command", &typed(agent)),
            ("reason", reason),
        ],
    )
}

/// Claude Code's structured error for a failed sign-in, on an assistant
/// message (2.1.332's `error` member).
pub const CLAUDE_ERROR: &str = "authentication_failed";

/// A failed Claude Code result that tells the person to sign in: "Not
/// logged in · Please run /login", "Login expired · Please run /login",
/// "Authentication required · Sign in again to continue" (2.1.332). An
/// external API key's refusal ("Invalid API key · Fix external API key")
/// is not one: signing in does not fix it.
pub fn claude_said(result: &str) -> bool {
    result.contains("Please run /login") || said_again(result)
}

/// A failed Codex turn whose message tells the person to sign in again:
/// "Your access token could not be refreshed. Please log out and sign in
/// again." and its kin (codex-cli 0.172.0).
pub fn codex_said(message: &str) -> bool {
    said_again(message) || message.contains("re-run `codex login`")
}

fn said_again(text: &str) -> bool {
    text.to_ascii_lowercase().contains("sign in again")
}

/// OpenCode's error type for a provider that refused the sign-in (2.3.2).
pub const OPENCODE_ERROR: &str = "provider.auth";

#[cfg(test)]
mod tests;
