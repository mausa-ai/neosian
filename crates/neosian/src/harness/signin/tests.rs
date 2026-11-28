//! Sign-in status answers and in-turn sign-in failures (X4c). The answers
//! are the clients' own, read from their code without running them:
//! Claude Code 2.1.332's `auth status` (JSON from `{loggedIn, authMethod,
//! apiProvider, ...}`, `process.exit(loggedIn ? 0 : 1)`; the signed-in
//! shape also as captured in record/experiments/cache-hourly-2026-10-30/
//! raw/auth-summary.json), codex-cli 0.172.0's `login status` lines on
//! stderr, and OpenCode 2.3.2's `provider.auth` error type.

use serde_json::{Map, Value};

use super::super::adapter::Agent;
use super::*;

const CLAUDE_IN: &str = r#"{
  "loggedIn": true,
  "authMethod": "claude.ai",
  "apiProvider": "firstParty",
  "email": "person@example.com",
  "orgId": "0a1b2c3d-4e5f-6789-abcd-ef0123456789",
  "subscriptionType": "max"
}"#;

const CLAUDE_OUT: &str = r#"{
  "loggedIn": false,
  "authMethod": "none",
  "apiProvider": "firstParty",
  "analyticsDisabled": false,
  "loginExpiresAt": null
}"#;

fn unknown(status: Status) -> String {
    match status {
        Status::Unknown(reason) => reason,
        other => panic!("not unknown: {other:?}"),
    }
}

#[test]
fn each_client_has_its_own_status_command_or_none() {
    assert_eq!(typed(Agent::ClaudeCode), "claude auth status");
    assert_eq!(typed(Agent::Codex), "codex login status");
    assert_eq!(command(Agent::OpenCode), None);
}

#[test]
fn claude_code_is_signed_out_only_when_its_answer_says_so() {
    let claude =
        |code, stdout: &str, stderr: &str| classify(Agent::ClaudeCode, code, stdout, stderr);
    assert_eq!(claude(Some(0), CLAUDE_IN, ""), Status::SignedIn);
    assert_eq!(claude(Some(1), CLAUDE_OUT, ""), Status::SignedOut);
    // A contradiction or anything else is unknown, never signed out.
    assert!(matches!(
        claude(Some(0), CLAUDE_OUT, ""),
        Status::Unknown(_)
    ));
    assert!(matches!(claude(Some(1), CLAUDE_IN, ""), Status::Unknown(_)));
    let malformed = unknown(claude(Some(1), "{\"loggedIn\": fal", ""));
    assert!(malformed.contains("exit status 1"), "{malformed}");
    let older = unknown(claude(Some(1), "", "error: unknown command 'auth'\n"));
    assert!(
        !older.contains("unknown command"),
        "nothing printed is shown: {older}"
    );
    let killed = unknown(claude(None, "", ""));
    assert!(killed.starts_with("exit status a signal"), "{killed}");
    // Only the expected exit code says signed out (X4f Q9).
    assert!(matches!(
        claude(Some(2), CLAUDE_OUT, ""),
        Status::Unknown(_)
    ));
}

#[test]
fn codex_is_signed_out_only_when_it_says_not_logged_in() {
    let codex = |code, stderr: &str| classify(Agent::Codex, code, "", stderr);
    assert_eq!(
        codex(Some(0), "Logged in using ChatGPT\n"),
        Status::SignedIn
    );
    assert_eq!(
        codex(Some(0), "Logged in using access token\n"),
        Status::SignedIn
    );
    assert_eq!(
        codex(Some(0), "API key configured - sk-proj-***ABCDE\n"),
        Status::SignedIn
    );
    assert_eq!(codex(Some(1), "Not logged in\n"), Status::SignedOut);
    assert!(matches!(
        codex(Some(0), "Not logged in\n"),
        Status::Unknown(_)
    ));
    // Codex itself cannot tell for some providers: unknown, so it starts.
    let deferred = codex(
        Some(0),
        "Authentication is checked when you send a request.\n",
    );
    assert!(matches!(deferred, Status::Unknown(_)));
    let network = unknown(codex(
        Some(1),
        "Error checking login status: error sending request for url (https://auth.openai.com/oauth/token)\n",
    ));
    assert!(!network.contains("Error checking"), "{network}");
    assert!(!network.contains("auth.openai.com"), "{network}");
    // Signed out only when the answer says that and nothing else (X4f Q9).
    let both = codex(Some(1), "Not logged in\nLogged in using ChatGPT\n");
    assert!(matches!(both, Status::Unknown(_)));
    assert!(matches!(
        codex(Some(2), "Not logged in\n"),
        Status::Unknown(_)
    ));
}

#[test]
fn an_unknown_answer_shows_no_credential_or_personal_detail() {
    let said = "Error checking login status: token sk-proj-abc123SECRET456 in /Users/someone/.codex/auth.json for person@example.com key=v4lue ********ABCDE\nsecond line s3cret";
    let shown = unknown(classify(Agent::Codex, Some(1), "", said));
    for secret in [
        "abc123SECRET456",
        "someone",
        "person@",
        "v4lue",
        "ABCDE",
        "s3cret",
    ] {
        assert!(!shown.contains(secret), "{secret} in {shown}");
    }
    assert!(!shown.contains("token"), "no word of the answer: {shown}");
    let short = unknown(classify(Agent::Codex, Some(1), "", "bearer abcdefghij\n"));
    assert!(!short.contains("abcdefghij"), "{short}");
    assert!(!scrub("\u{1b}[31mred\u{1b}[0m ok").contains('\u{1b}'));
    assert!(scrub(&"word ".repeat(100)).len() <= 160);
    let json = unknown(classify(Agent::ClaudeCode, Some(2), CLAUDE_IN, ""));
    assert!(!json.contains("person@example.com"), "{json}");
}

fn object(line: &str) -> Map<String, Value> {
    match serde_json::from_str(line).unwrap() {
        Value::Object(map) => map,
        _ => panic!("not an object"),
    }
}

#[test]
fn a_sign_in_failure_in_a_turn_is_the_same_instruction() {
    let instruction = |agent| Event::SignIn(super::instruction(agent));
    let assistant = r#"{"type":"assistant","message":{"content":[{"type":"text","text":"Login expired · Please run /login"}]},"parent_tool_use_id":null,"error":"authentication_failed"}"#;
    let read = super::super::claude::read(&object(assistant));
    assert_eq!(read[0], instruction(Agent::ClaudeCode));
    for result in [
        "Not logged in · Please run /login",
        "Authentication required · Sign in again to continue",
    ] {
        let line = serde_json::json!({"type": "result", "subtype": "success", "is_error": true, "result": result});
        let read = super::super::claude::read(line.as_object().unwrap());
        assert_eq!(read, [instruction(Agent::ClaudeCode)], "{result}");
    }
    let key = r#"{"type":"result","subtype":"success","is_error":true,"result":"Invalid API key · Fix external API key"}"#;
    assert!(matches!(
        super::super::claude::read(&object(key))[..],
        [Event::Failure(_)]
    ));

    let refresh = r#"{"type":"turn.failed","error":{"message":"Your access token could not be refreshed. Please log out and sign in again."}}"#;
    assert_eq!(
        super::super::codex::read(&object(refresh)),
        [instruction(Agent::Codex)]
    );
    let quota = r#"{"type":"turn.failed","error":{"message":"quota exceeded"}}"#;
    assert!(matches!(
        super::super::codex::read(&object(quota))[..],
        [Event::Failure(_)]
    ));

    let auth = r#"{"type":"error","sessionID":"ses_1","error":{"type":"provider.auth","message":"invalid x-api-key"}}"#;
    let read = super::super::opencode::read(&object(auth));
    assert_eq!(read[1], instruction(Agent::OpenCode));
    for agent in Agent::ALL {
        let sentence = super::instruction(agent);
        assert!(sentence.contains("outside the child"), "{sentence}");
        assert!(!sentence.contains('\u{2014}'));
    }
    assert!(super::instruction(Agent::Codex).contains("codex login"));
    assert!(super::instruction(Agent::ClaudeCode).contains("claude auth login"));
    assert!(super::instruction(Agent::OpenCode).contains("opencode auth login"));
}
