//! The old release's side of the handover, with a stand-in updater and new release.
#![cfg(unix)]

use super::*;
use crate::testing::Scratch;
use clap::Parser;
use std::{fs, os::unix::fs::PermissionsExt};

const HINT_TEXT: &str = "run neosian setup --refresh";

#[derive(Parser)]
struct Command {
    #[command(flatten)]
    args: Update,
}

fn newer() -> String {
    let (base, patch) = crate::version::VERSION.rsplit_once('.').unwrap();
    format!("{base}.{}", patch.parse::<u64>().unwrap() + 1)
}

/// An installation whose updater installs `next`: `version` reports the newer
/// release, and any other call records its arguments and runs `body`.
fn installation(scratch: &Scratch, body: &str) -> Context {
    let (context, mut receipt) = tests::installation(scratch);
    receipt["version"] = json!(crate::version::VERSION);
    scratch.plant("neosian-receipt.json", &receipt.to_string());
    scratch.plant("install dir/neosian", "#!/bin/sh\nexit 0\n");
    fs::set_permissions(&context.executable, fs::Permissions::from_mode(0o700)).unwrap();
    let newer = newer();
    scratch.plant(
        "install dir/next",
        &format!(
            "#!/bin/sh\n[ \"$1\" = version ] && echo 'neosian v{newer}' && exit\n\
             echo \"$*\" >> \"$(dirname \"$0\")/handover\"\n{body}\n"
        ),
    );
    scratch.plant(
        "install dir/neosian-update",
        "#!/bin/sh\ncp \"$(dirname \"$0\")/next\" \"$(dirname \"$0\")/neosian\"\n",
    );
    context
}

fn run(context: &Context, json: bool) -> (u8, String, String) {
    let mut argv = vec!["update"];
    if json {
        argv.push("--json");
    }
    let args = Command::try_parse_from(argv).unwrap().args;
    let latest = version::Version::parse(&newer()).unwrap();
    let (mut out, mut err) = (vec![], vec![]);
    let code = complete(&args, context, Some(&latest), "off", &mut out, &mut err).unwrap();
    let text = |b: Vec<u8>| String::from_utf8(b).unwrap();
    (code, text(out), text(err))
}

fn handover(scratch: &Scratch) -> String {
    fs::read_to_string(scratch.path().join("install dir/handover")).unwrap_or_default()
}

fn installed(context: &Context) -> bool {
    fs::read_to_string(&context.executable)
        .unwrap()
        .contains(&format!("neosian v{}", newer()))
}

#[test]
fn a_verified_install_hands_over_exactly_setup_refresh() {
    let scratch = Scratch::new("handover-success");
    let context = installation(
        &scratch,
        "[ \"$3\" = --json ] && echo '{\"success\": true, \"clients\": []}' && exit\n\
         echo 'Restart OpenCode to load the release.'; echo 'a note' >&2",
    );
    let (code, out, err) = run(&context, true);
    assert_eq!(code, 0, "{err}");
    let report: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(report["action"], "apply");
    assert_eq!(report["refresh"], json!({"success": true, "clients": []}));
    let (code, out, err) = run(&context, false);
    assert_eq!(code, 0, "{err}");
    let restart = format!(
        "\nRestart OpenCode to load the release.\nneosian {} is installed; restart running clients so they use it.\n",
        newer()
    );
    assert!(out.ends_with(&restart), "{out}");
    let started = format!(
        "Refreshing existing registrations: {}\n",
        crate::clients::context::shell_join(&[
            context.executable.clone(),
            "setup".into(),
            "--refresh".into()
        ])
    );
    assert_eq!(err, format!("{started}a note\n"));
    assert_eq!(
        handover(&scratch),
        "setup --refresh --json\nsetup --refresh\n"
    );
}

#[test]
fn a_failed_client_keeps_the_install_and_exits_1() {
    let scratch = Scratch::new("handover-client-failed");
    let context = installation(
        &scratch,
        "echo '{\"success\": false, \"clients\": [{\"result\": \"failed\"}]}'; exit 1",
    );
    let (code, out, _) = run(&context, true);
    assert_eq!(code, 1);
    let report: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(report["refresh"]["success"], false);
    assert_eq!(report["refresh"]["clients"][0]["result"], "failed");
    assert!(installed(&context));
    let (code, out, _) = run(&context, false);
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("\"result\": \"failed\""), "{out}");
}

#[test]
fn a_rejected_or_garbled_handover_is_reported_and_never_rolled_back() {
    // Text mode trusts the release's own words, so only the exit status can fail it.
    for (body, why, text_fails) in [
        (
            "echo 'error: unexpected argument' >&2; exit 2",
            "exit 2",
            true,
        ),
        ("echo 'not json'", "not one refresh object", false),
        (
            "echo '{\"success\": false}'",
            "not one refresh object",
            false,
        ),
        ("kill -9 $$", "without an exit code", true),
    ] {
        let scratch = Scratch::new("handover-rejected");
        let context = installation(&scratch, body);
        let (code, out, err) = run(&context, true);
        assert_eq!(code, 1, "{body}");
        let report: Value = serde_json::from_str(&out).unwrap();
        let refresh = &report["refresh"];
        let error = refresh["error"].as_str().unwrap();
        assert_eq!(refresh["success"], false);
        assert!(
            error.contains(why) && error.ends_with("it stays installed"),
            "{error}"
        );
        assert_eq!(refresh["hint"], HINT_TEXT);
        assert!(err.contains(&format!("hint: {HINT_TEXT}")), "{err}");
        assert!(installed(&context));
        let (code, _, err) = run(&context, false);
        assert_eq!(code, u8::from(text_fails), "{body}");
        assert_eq!(
            err.contains("did not complete the registration refresh"),
            text_fails
        );
    }
}

#[test]
fn a_failed_install_never_hands_over() {
    let scratch = Scratch::new("handover-install-failed");
    let context = installation(&scratch, "exit 0");
    scratch.plant("install dir/neosian-update", "#!/bin/sh\nexit 7\n");
    let (code, out, _) = run(&context, true);
    assert_eq!(code, 7);
    let report: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(report["refresh"], Value::Null);
    // An install that does not report the checked release is not handed over to either.
    scratch.plant("install dir/neosian-update", "#!/bin/sh\nexit 0\n");
    let (code, _, err) = run(&context, false);
    assert_eq!(code, 1);
    assert!(err.contains("does not report the checked release"));
    assert_eq!(handover(&scratch), "");
}

#[test]
fn text_output_passes_through_while_the_refresh_runs() {
    let scratch = Scratch::new("handover-stream");
    let marker = scratch.path().join("seen");
    // The release waits for a marker that only a streaming reader can create.
    scratch.plant(
        "release",
        &format!(
            "#!/bin/sh\necho first\ni=0\nwhile [ ! -e '{}' ]; do\n  i=$((i + 1)); [ $i -gt 100 ] && exit 3; sleep 0.1\ndone\necho second\n",
            marker.display()
        ),
    );
    let release = scratch.path().join("release");
    fs::set_permissions(&release, fs::Permissions::from_mode(0o700)).unwrap();
    let mut seen: Vec<(handover::Stream, Vec<u8>)> = vec![];
    let (code, report) = handover::refresh(
        &release.display().to_string(),
        false,
        &mut |stream, bytes| {
            if stream == handover::Stream::Out && bytes.starts_with(b"first") {
                fs::write(&marker, "")?;
            }
            seen.push((stream, bytes.to_vec()));
            Ok(())
        },
    )
    .unwrap();
    assert_eq!((code, report), (0, Value::Null));
    let text = |wanted| -> String {
        seen.iter()
            .filter(|(stream, _)| *stream == wanted)
            .map(|(_, bytes)| String::from_utf8_lossy(bytes).into_owned())
            .collect()
    };
    assert_eq!(text(handover::Stream::Out), "first\nsecond\n");
    assert!(text(handover::Stream::Err).starts_with("Refreshing existing registrations: "));
}

#[test]
fn a_closed_stderr_loses_the_start_line_never_the_refresh() {
    let scratch = Scratch::new("handover-closed-stderr");
    let marker = scratch.path().join("ran");
    scratch.plant(
        "release",
        &format!(
            "#!/bin/sh\ntouch '{}'\necho 'a report'\necho 'a note' >&2\n",
            marker.display()
        ),
    );
    let release = scratch.path().join("release");
    fs::set_permissions(&release, fs::Permissions::from_mode(0o700)).unwrap();
    let mut out = vec![];
    let (code, report) = handover::refresh(
        &release.display().to_string(),
        false,
        &mut |stream, bytes| match stream {
            handover::Stream::Err => Err(io::Error::from(io::ErrorKind::BrokenPipe)),
            handover::Stream::Out => {
                out.extend_from_slice(bytes);
                Ok(())
            }
        },
    )
    .unwrap();
    assert!(marker.exists());
    assert_eq!((code, report), (0, Value::Null));
    assert_eq!(out, b"a report\n");
}
