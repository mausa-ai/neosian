//! The literal binary, as a shell or an installer meets it.

use std::process::Command;

fn neosian(args: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_neosian"))
        .args(args)
        .output()
        .expect("the binary runs")
}

#[test]
fn version_is_one_greppable_line() {
    let output = neosian(&["version"]);
    assert!(output.status.success());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap(),
        format!("neosian v{}\n", env!("CARGO_PKG_VERSION"))
    );
}

#[test]
fn docs_lists_then_prints_a_page() {
    let listing = neosian(&["docs"]);
    assert!(listing.status.success());
    let stdout = String::from_utf8(listing.stdout).unwrap();
    assert_eq!(stdout.lines().count(), 15);
    assert!(stdout.lines().any(|line| line.starts_with("changes ")));
    assert!(stdout.lines().any(|line| line.starts_with("governance ")));
    assert!(stdout.lines().any(|line| line.starts_with("knowledge ")));
    assert!(stdout.lines().any(|line| line.starts_with("profiles ")));
    assert!(stdout.starts_with("quickstart  "));
    assert_eq!(
        String::from_utf8(listing.stderr).unwrap(),
        "hint: neosian docs <topic> prints a page\n"
    );
    let page = neosian(&["docs", "cli"]);
    assert!(page.status.success());
    let body = String::from_utf8(page.stdout).unwrap();
    assert!(body.starts_with('#') && body.ends_with('\n'));
}

#[test]
fn a_bad_invocation_exits_two() {
    assert_eq!(neosian(&["docs", "nope"]).status.code(), Some(2));
    assert_eq!(neosian(&["nope"]).status.code(), Some(2));
}

#[test]
fn the_container_probe_needs_no_token_or_store() {
    // A reserved but unserved port refuses promptly. The deliberately bad
    // DSN and absent token would fail server construction before any request.
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port().to_string();
    drop(listener);
    let output = Command::new(env!("CARGO_BIN_EXE_neosian"))
        .args(["serve", "--healthcheck", "--port", &port])
        .env_remove("NEOSIAN_SERVE_TOKEN")
        .env("NEOSIAN_POSTGRES_DSN", "not-a-dsn")
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert!(output.stdout.is_empty());
    assert!(
        String::from_utf8(output.stderr)
            .unwrap()
            .starts_with("healthcheck:")
    );
    assert_eq!(
        neosian(&["serve", "--healthcheck", "--port", "0"])
            .status
            .code(),
        Some(2)
    );
}
