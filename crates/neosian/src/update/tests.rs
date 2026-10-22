use super::*;
use crate::testing::Scratch;
use serde_json::Value;
use std::{collections::BTreeMap, fs, io::Read, net::TcpListener, thread};
use version::Version;

fn installation(scratch: &Scratch) -> (Context, Value) {
    let root = scratch.path();
    let name = if cfg!(windows) {
        "neosian.exe"
    } else {
        "neosian"
    };
    let updater = if cfg!(windows) {
        "neosian-update.exe"
    } else {
        "neosian-update"
    };
    scratch.plant(&format!("install dir/{name}"), "");
    scratch.plant(&format!("install dir/{updater}"), "");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(
            root.join(format!("install dir/{updater}")),
            fs::Permissions::from_mode(0o700),
        )
        .unwrap();
    }
    let context = Context {
        home: root.into(),
        cwd: root.into(),
        executable: root
            .join(format!("install dir/{name}"))
            .display()
            .to_string(),
        env: BTreeMap::from([("AXOUPDATER_CONFIG_PATH".into(), root.display().to_string())]),
        directories: BTreeMap::new(),
    };
    let receipt = json!({"install_prefix": root.join("install dir"), "binaries": [name], "version": "1.2.3", "provider": {"source": "cargo-dist", "version": "0.32.0"}, "source": {"release_type": "github", "owner": "mausa-ai", "name": "neosian", "app_name": "neosian"}});
    scratch.plant("neosian-receipt.json", &receipt.to_string());
    (context, receipt)
}

#[test]
fn release_order_and_the_fences() {
    let scratch = Scratch::new("update-fences");
    let (context, mut receipt) = installation(&scratch);
    let action = |v: &str, applying| plan("1.2.3", Version::parse(v).as_ref(), &context, applying);
    assert_eq!(action("1.2.3", true).action, "current");
    assert_eq!(action("1.1.0", true).action, "current");
    assert_eq!(action("1.2.4", false).action, "notify");
    let apply = action("1.2.4", true);
    assert_eq!(apply.action, "apply");
    assert_eq!(&apply.argv[1..], ["--tag", "v1.2.4"]);
    assert!(Path::new(&apply.argv[0]).is_absolute());
    assert!(apply.line.contains("'")); // The directory contains a space.
    assert!(action("2.0.0", true).line.contains("new major"));
    assert!(action("1.3.0rc1", true).line.contains("pre-release"));
    assert_eq!(action("invalid", true).action, "unreachable");
    assert!(Version::parse("1.2.3rc9") < Version::parse("1.2.3"));
    assert_eq!(Version::parse("1.2.3-rc.9"), Version::parse("1.2.3rc9"));
    for bad in [
        "1.2",
        "1.2.3.4",
        "1.2.3rc",
        "1.2.3rc-1",
        "1.2.3-rc.",
        "1.2.3-rc.1.2",
        "1.2.3\n",
        "+1.2.3",
    ] {
        assert!(Version::parse(bad).is_none());
    }
    receipt["version"] = json!("1.2.3-rc.1");
    scratch.plant("neosian-receipt.json", &receipt.to_string());
    assert_eq!(
        plan(
            "1.2.3-rc.1",
            Version::parse("1.2.3").as_ref(),
            &context,
            true
        )
        .action,
        "apply"
    );
}

#[test]
fn receipt_ownership_prevents_updating_another_installation() {
    let scratch = Scratch::new("update-receipt");
    let (mut context, original) = installation(&scratch);
    assert!(receipt::updater(&context, "1.2.3").is_ok());
    for key in [
        "install_prefix",
        "version",
        "binaries",
        "source",
        "provider",
    ] {
        let mut receipt = original.clone();
        receipt[key] = json!("foreign");
        scratch.plant("neosian-receipt.json", &receipt.to_string());
        assert!(receipt::updater(&context, "1.2.3").is_err(), "{key}");
    }
    scratch.plant("neosian-receipt.json", &original.to_string());
    let updater = receipt::updater(&context, "1.2.3").unwrap();
    fs::remove_file(updater).unwrap();
    assert!(
        receipt::updater(&context, "1.2.3")
            .unwrap_err()
            .contains("missing")
    );
    context
        .env
        .insert("NEOSIAN_INSTALL".into(), "container".into());
    let result = plan("1.2.3", Version::parse("1.2.4").as_ref(), &context, true);
    assert_eq!(result.action, "refuse");
    assert!(
        result
            .line
            .ends_with("docker pull ghcr.io/mausa-ai/neosian:1.2.4")
    );
    fs::remove_file(scratch.path().join("neosian-receipt.json")).unwrap();
    context.env.remove("NEOSIAN_INSTALL");
    assert!(
        receipt::updater(&context, "1.2.3")
            .unwrap_err()
            .contains("no cargo-dist")
    );
}

#[test]
fn only_a_published_release_with_an_installer_qualifies() {
    let installer = if cfg!(windows) {
        "neosian-installer.ps1"
    } else {
        "neosian-installer.sh"
    };
    let good = json!({"tag_name": "v1.2.4", "draft": false, "prerelease": false, "assets": [{"name": installer}]});
    assert_eq!(check::release(&good).unwrap().to_string(), "1.2.4");
    for (key, value) in [
        ("draft", json!(true)),
        ("prerelease", json!(true)),
        ("assets", json!([])),
        ("tag_name", json!("v1.3.0rc1")),
    ] {
        let mut bad = good.clone();
        bad[key] = value;
        assert!(check::release(&bad).is_none());
    }
    assert!(check::release(&Value::Null).is_none());
}

#[test]
fn the_release_check_handles_http_errors_and_bad_data() {
    for (status, body) in [
        (200, "not JSON"),
        (503, "{}"),
        (200, "{}"),
        (302, "{}"),
        (
            200,
            r#"{"tag_name":"v1.2.4","draft":false,"prerelease":false,"assets":[{"name":"neosian-installer.sh"},{"name":"neosian-installer.ps1"}]}"#,
        ),
    ] {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let server = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            let mut buffer = [0; 4096];
            let count = socket.read(&mut buffer).unwrap();
            let request = String::from_utf8_lossy(&buffer[..count]).to_lowercase();
            assert!(
                request.contains("user-agent: neosian/")
                    && request.contains("accept: application/vnd.github+json")
            );
            write!(
                socket,
                "HTTP/1.1 {status} Test\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        });
        let result = latest(&url);
        assert_eq!(result.is_some(), body.contains("tag_name"));
        server.join().unwrap();
    }
}

#[test]
fn cache_is_separate_bounded_and_does_not_trust_a_future_stamp() {
    let scratch = Scratch::new("update-stamp");
    let now = Stamp::parse("2026-10-22T12:00:00Z").unwrap();
    let version = Version::parse("1.2.4").unwrap();
    scratch.plant("update-check", "2026-10-22T12:00:00Z 99.0.0\n");
    assert!(cached(scratch.path(), now).is_none());
    stamp(scratch.path(), &version, now).unwrap();
    assert_eq!(cached(scratch.path(), now.after(23 * 3600)), Some(version));
    assert!(cached(scratch.path(), now.after(24 * 3600)).is_none());
    assert!(cached(scratch.path(), now.after(-1)).is_none());
    scratch.plant("binary-update-check", "broken");
    assert!(cached(scratch.path(), now).is_none());
    assert_eq!(mode(scratch.path()), "off");
    scratch.plant("config.toml", "[broken");
    assert_eq!(mode(scratch.path()), "off");
}

#[test]
fn agent_verbs_pipes_and_json_never_check_for_updates() {
    for verb in [
        "record", "mcp", "memory", "serve", "search", "continue", "version", "docs", "setup",
        "update",
    ] {
        assert!(!human_door(verb, true, false));
    }
    for verb in ["status", "configure"] {
        assert!(human_door(verb, true, false));
        assert!(!human_door(verb, false, false));
        assert!(!human_door(verb, true, true));
    }
}

#[cfg(unix)]
#[test]
fn the_updater_gets_the_checked_tag_and_its_output_stays_off_stdout() {
    let scratch = Scratch::new("update-process");
    let (context, _) = installation(&scratch);
    scratch.plant(
        "install dir/neosian-update",
        "#!/bin/sh\nprintf '%s\\n' \"$@\"\nprintf 'failed\\n' >&2\nexit 7\n",
    );
    let decision = plan("1.2.3", Version::parse("1.2.4").as_ref(), &context, true);
    let mut diagnostic = vec![];
    assert_eq!(apply(&decision.argv, &mut diagnostic).unwrap(), 7);
    assert_eq!(
        String::from_utf8(diagnostic).unwrap(),
        "--tag\nv1.2.4\nfailed\n"
    );
}

#[cfg(unix)]
#[test]
fn a_successful_updater_must_actually_replace_the_release() {
    use std::os::unix::fs::PermissionsExt;
    let scratch = Scratch::new("update-verification");
    let (context, _) = installation(&scratch);
    scratch.plant("install dir/neosian-update", "#!/bin/sh\nexit 0\n");
    scratch.plant("install dir/neosian", "#!/bin/sh\necho 'neosian v1.2.3'\n");
    fs::set_permissions(&context.executable, fs::Permissions::from_mode(0o700)).unwrap();
    let decision = plan("1.2.3", Version::parse("1.2.4").as_ref(), &context, true);
    let mut diagnostic = vec![];
    assert_eq!(install(&decision, &context, &mut diagnostic).unwrap(), 1);
    assert!(
        String::from_utf8(diagnostic)
            .unwrap()
            .contains("does not report")
    );
    scratch.plant("install dir/neosian", "#!/bin/sh\necho 'neosian v1.2.4'\n");
    assert_eq!(install(&decision, &context, &mut vec![]).unwrap(), 0);
    // Losing the updater after the decision is a diagnostic, not a second JSON value.
    fs::remove_file(&decision.argv[0]).unwrap();
    let mut diagnostic = vec![];
    assert_eq!(install(&decision, &context, &mut diagnostic).unwrap(), 1);
    assert!(
        String::from_utf8(diagnostic)
            .unwrap()
            .starts_with("error: ")
    );
}
