//! What a read-only child is given of neosian (#431): every mount
//! read-only, or only the tools that cannot change state; Codex's other
//! servers and apps off.

use std::path::Path;

use super::*;

fn server() -> McpServer {
    McpServer {
        command: "/opt/neosian".to_string(),
        args: ["mcp", "--root", "/r", "--actor", "mcp:claude-code"]
            .map(String::from)
            .to_vec(),
        mounted: true,
    }
}

fn request<'a>(agent: Agent, mode: Mode, keys: &'a [CodexKey]) -> Request<'a> {
    Request {
        agent,
        mode,
        model: None,
        directory: Path::new("/work/dir"),
        resume: None,
        new_session: "5eab6b3f-d0af-49c3-9c0c-a9b47c2c8ebe",
        private: Path::new("/tmp/neosian-harness-x"),
        codex_keys: keys,
        opencode_config: None,
        parent: None,
        server: None,
        codex_off: &[],
        confined: false,
    }
}

#[test]
fn a_read_only_child_gets_neosian_read_only_or_only_its_state_free_tools() {
    let own = server();
    let mut start = request(Agent::ClaudeCode, Mode::ReadOnly, &[]);
    start.server = Some(&own);
    let mounted = plan(&start).unwrap();
    let at = mounted
        .argv
        .iter()
        .position(|w| w == "--allowedTools")
        .unwrap();
    assert_eq!(mounted.argv[at + 1], "mcp__neosian");
    assert_eq!(mounted.argv[at + 2], "--disallowedTools");
    let unmounted = McpServer {
        mounted: false,
        ..server()
    };
    start.server = Some(&unmounted);
    let named = plan(&start).unwrap();
    let at = named
        .argv
        .iter()
        .position(|w| w == "--allowedTools")
        .unwrap();
    let allowed: Vec<&str> = named.argv[at + 1..at + 11]
        .iter()
        .map(String::as_str)
        .collect();
    assert_eq!(allowed[0], "mcp__neosian__list_skills");
    assert!(
        !allowed
            .iter()
            .any(|t| t.ends_with("__memory") || t.ends_with("__messages"))
    );
    assert_eq!(named.argv[at + 11], "--disallowedTools");

    let keys = [CodexKey {
        key: "neosian-memory",
        disabled: vec![],
        transport: super::super::transport::Transport::Stdio,
    }];
    let off = ["playwright".to_string(), "sentry".to_string()];
    let mut codex = request(Agent::Codex, Mode::ReadOnly, &keys);
    let codex_server = McpServer {
        command: "/x".into(),
        args: vec!["mcp".into(), "--mount".into(), "scope=s,path=p,ro".into()],
        mounted: true,
    };
    codex.server = Some(&codex_server);
    codex.codex_off = &off;
    let argv = plan(&codex).unwrap().argv;
    assert!(argv.windows(2).any(|pair| pair == ["--disable", "apps"]));
    assert!(argv.contains(&"mcp_servers.playwright.enabled=false".to_string()));
    assert!(argv.contains(&"mcp_servers.sentry.enabled=false".to_string()));
    assert!(argv.contains(
        &r#"mcp_servers.neosian-memory.args=["mcp","--mount","scope=s,path=p,ro"]"#.to_string()
    ));
    // This binary, never a wrapper the entry was registered through (#432).
    assert!(argv.contains(&r#"mcp_servers.neosian-memory.command="/x""#.to_string()));
    assert!(!argv.iter().any(|w| w.contains("enabled_tools")));
    let profile_server = McpServer {
        mounted: false,
        ..codex_server.clone()
    };
    codex.server = Some(&profile_server);
    let argv = plan(&codex).unwrap().argv;
    assert!(
        argv.iter()
            .any(|w| w.starts_with("mcp_servers.neosian-memory.enabled_tools=[\"list_skills\""))
    );
    codex.mode = Mode::Write;
    let argv = plan(&codex).unwrap().argv;
    assert!(!argv.iter().any(|w| w.contains(".enabled=false")
        || w.contains(".args=")
        || w.contains(".command=")
        || w == "apps"));
}

#[test]
fn a_codex_server_listed_without_a_usable_name_is_kept_unaddressable() {
    use super::super::codex::server_names;
    let listed = server_names(
        r#"[{"name":"playwright"},{"enabled":true},{"name":7},{"name":null},"sentry"]"#,
    )
    .unwrap();
    // By position only: an entry may carry a server's environment.
    assert_eq!(
        listed,
        [Ok("playwright".to_string()), Err(2), Err(3), Err(4), Err(5)]
    );
    assert_eq!(server_names(r#"{"servers":[]}"#), None);
}
