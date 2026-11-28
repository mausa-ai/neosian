//! Codex registrations by transport (X4c): each entry is classified and
//! gets only the fields Codex accepts for its transport.

use std::path::Path;

use serde_json::json;

use super::super::adapter::{Agent, Mode, Request, plan};
use super::*;

/// `codex mcp get neosian --json` as codex-cli 0.169.0 printed it
/// (record/x2a-real-cli-2026-11-20/07-after-first-refresh.txt), paths cut.
const STDIO: &str = r#"{"name":"neosian","enabled":true,"disabled_reason":null,"transport":{"type":"stdio","command":"/bin/neosian","args":["mcp","--actor","mcp:codex"],"env":null,"env_vars":[],"cwd":null},"enabled_tools":["memory","recall_turn","search_history"],"disabled_tools":null,"startup_timeout_sec":null,"tool_timeout_sec":null}"#;

/// The same entry over HTTP, in the shape Codex's configuration gives a
/// `streamable_http` transport; its header carries a credential.
const HTTP: &str = r#"{"name":"neosian-cloud","enabled":true,"disabled_reason":null,"transport":{"type":"streamable_http","url":"https://cloud.example/mcp","bearer_token_env_var":null,"http_headers":{"Authorization":"Bearer s3cret-token"},"env_http_headers":null},"enabled_tools":null,"disabled_tools":["upkeep_apply"],"startup_timeout_sec":null,"tool_timeout_sec":null}"#;

fn server(mounted: bool) -> McpServer {
    McpServer {
        command: "/this/neosian".into(),
        args: vec!["mcp".into(), "--mount".into(), "scope=s,path=p,ro".into()],
        mounted,
    }
}

fn argv(mode: Mode, keys: &[CodexKey], server: Option<&McpServer>) -> Vec<String> {
    let request = Request {
        agent: Agent::Codex,
        mode,
        model: None,
        directory: Path::new("/work/dir"),
        resume: None,
        new_session: "5eab6b3f-d0af-49c3-9c0c-a9b47c2c8ebe",
        private: Path::new("/tmp/neosian-harness-x"),
        codex_keys: keys,
        opencode_config: None,
        parent: None,
        server,
        codex_off: &[],
        confined: false,
    };
    plan(&request).unwrap().argv
}

fn overrides(argv: &[String]) -> Vec<&str> {
    argv.windows(2)
        .filter(|pair| pair[0] == "-c")
        .map(|pair| pair[1].as_str())
        .filter(|value| value.starts_with("mcp_servers."))
        .collect()
}

#[test]
fn each_entry_is_classified_by_its_transport() {
    let stdio: Value = serde_json::from_str(STDIO).unwrap();
    let http: Value = serde_json::from_str(HTTP).unwrap();
    assert_eq!(transport(&stdio), Ok(Transport::Stdio));
    assert_eq!(transport(&http), Ok(Transport::Http));
    let unknown = |entry: Value| transport(&entry).unwrap_err();
    assert_eq!(unknown(json!({"transport": {"type": "sse"}})), "sse");
    assert_eq!(unknown(json!({"transport": {}})), "missing");
    assert_eq!(unknown(json!({"name": "x"})), "missing");
    assert_eq!(unknown(json!({"transport": {"type": 7}})), "unreadable");
    assert_eq!(
        unknown(json!({"transport": {"type": "Bearer s3cret token"}})),
        "unreadable"
    );
    let listed = listed(&format!(
        "[{STDIO},{HTTP},{{\"transport\":{{\"type\":\"ws\"}}}}]"
    ));
    assert_eq!(
        listed,
        [Ok(Transport::Stdio), Ok(Transport::Http), Err("ws".into())]
    );
    assert!(super::listed("not json").is_empty());
}

#[test]
fn a_key_carries_its_disabled_tools_and_transport_and_never_its_headers() {
    let found = key("neosian-cloud", HTTP).unwrap().unwrap();
    assert_eq!(found.transport, Transport::Http);
    assert_eq!(found.disabled, ["upkeep_apply"]);
    assert_eq!(
        key("neosian", STDIO).unwrap().unwrap().disabled,
        Vec::<String>::new()
    );
    assert_eq!(key("neosian", "not json"), None);
    assert_eq!(key("neosian", "[1]"), None);
    let refused = key(
        "neosian",
        r#"{"transport":{"type":"websocket","url":"wss://u:s3cret@x"}}"#,
    );
    assert_eq!(refused, Some(Err("websocket".into())));
}

#[test]
fn an_http_entry_gets_only_fields_codex_accepts_for_it_in_both_modes() {
    for name in KEYS {
        let http = [CodexKey {
            key: name,
            disabled: vec!["upkeep_apply".into()],
            transport: Transport::Http,
        }];
        let write = argv(Mode::Write, &http, None);
        assert_eq!(
            overrides(&write),
            [format!(
                "mcp_servers.{name}.disabled_tools=[\"upkeep_apply\",\"harness\"]"
            )],
            "{name}"
        );
        for mounted in [true, false] {
            let own = server(mounted);
            let read_only = argv(Mode::ReadOnly, &http, Some(&own));
            assert_eq!(
                overrides(&read_only),
                [format!("mcp_servers.{name}.enabled=false")],
                "{name}"
            );
        }
    }
}

#[test]
fn a_stdio_entry_keeps_its_fields_and_only_ours_runs_this_binary() {
    let own = server(false);
    for name in OWN {
        let stdio = [CodexKey {
            key: name,
            disabled: Vec::new(),
            transport: Transport::Stdio,
        }];
        let write = argv(Mode::Write, &stdio, None);
        assert_eq!(
            overrides(&write),
            [
                format!("mcp_servers.{name}.env.NEOSIAN_HARNESS_CHILD=\"1\""),
                format!("mcp_servers.{name}.disabled_tools=[\"harness\"]"),
            ]
        );
        let read_only = argv(Mode::ReadOnly, &stdio, Some(&own));
        let fields = overrides(&read_only);
        assert_eq!(fields.len(), 5, "{fields:?}");
        assert_eq!(
            fields[2],
            format!("mcp_servers.{name}.command=\"/this/neosian\"")
        );
        assert!(fields[3].starts_with(&format!("mcp_servers.{name}.args=[\"mcp\"")));
        assert!(fields[4].starts_with(&format!("mcp_servers.{name}.enabled_tools=[")));
    }
    // The hosted entry over stdio is not this server: off in read-only.
    let host = [CodexKey {
        key: "neosian-cloud",
        disabled: Vec::new(),
        transport: Transport::Stdio,
    }];
    assert_eq!(
        overrides(&argv(Mode::ReadOnly, &host, Some(&own))),
        ["mcp_servers.neosian-cloud.enabled=false"]
    );
    assert_eq!(
        overrides(&argv(Mode::Write, &host, None))[0],
        "mcp_servers.neosian-cloud.env.NEOSIAN_HARNESS_CHILD=\"1\""
    );
}

#[test]
fn no_path_switches_a_server_on_or_gives_http_a_stdio_field() {
    let own = server(true);
    let keys: Vec<CodexKey> = KEYS
        .iter()
        .flat_map(|&key| {
            [Transport::Stdio, Transport::Http].map(|transport| CodexKey {
                key,
                disabled: Vec::new(),
                transport,
            })
        })
        .collect();
    for key in &keys {
        for (mode, server) in [(Mode::Write, None), (Mode::ReadOnly, Some(&own))] {
            let fields = super::fields(key, server);
            assert!(
                !fields.iter().any(|f| f.ends_with(".enabled=true")),
                "{fields:?}"
            );
            if key.transport == Transport::Http {
                let stdio_only = [".env.", ".command=", ".args=", ".cwd=", ".env_vars="];
                assert!(
                    !fields
                        .iter()
                        .any(|f| stdio_only.iter().any(|s| f.contains(s))),
                    "{mode:?} {fields:?}"
                );
            }
        }
    }
}
