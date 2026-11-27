//! What a lander keeps of a refusal and when it times it (rulings G3, G5
//! and G6 of the T1e1 plan), over HTTP against `http.rs`: a message kept in
//! a destination's record or a held turn's reason has the client's token
//! removed and at most 512 scalars; a URL a hook hands over is in the
//! lander's environment, never its arguments; a wait or a block is timed
//! from when its refusal arrived; and in the attempt at a blocked
//! destination a refused probe keeps the turn and blocks again, with
//! nothing discarded or held.

use std::ffi::OsStr;
use std::fs;
use std::path::Path;
use std::sync::atomic::{AtomicI64, Ordering};
use std::time::{Duration, SystemTime};

use serde_json::{Value, json};

use super::super::cap::{Cap, Patience};
use super::super::destination::{SIX_HOURS, key, read, stamp};
use super::super::draft::micros_z;
use super::super::gate::{Gated, land};
use super::super::kept::{REMOVED, credentials, kept};
use super::super::queue::Queue;
use super::super::rules::{Block, Stop};
use super::super::slots::directory;
use super::super::spawn::{LANDER_URL, Lander, command};
use super::super::state::describe;
use super::super::turn::Here;
use super::ID;
use super::attempt::{at, is_part, landing, queued, refusing};
use super::http::{Server, refusal, written};
use crate::record::land::here;
use crate::record::settings::Settings;
use crate::remote::selection::Selection;
use crate::testing::Scratch;

/// A refusal that echoes the request's authorization and runs long.
fn echoing() -> String {
    format!("Authorization: Bearer test-token {}", "é".repeat(600))
}

fn record(settings: &Settings, home: &Path) -> super::super::destination::Record {
    read(&directory(home), &key(&describe(&settings.store))).expect("a record")
}

/// Kept: no token, and at most 512 scalars.
fn clean(message: &str) {
    assert!(!message.contains("test-token"), "{message}");
    assert!(message.contains(REMOVED), "{message}");
    assert_eq!(message.chars().count(), 512, "{message}");
}

#[test]
fn a_message_is_kept_without_the_client_s_credentials_and_cut_to_512_scalars() {
    let remote = Selection::Remote {
        url: "https://alice:hunter2@example.test/base?key=s3cret#top".into(),
        token: "t0ken".into(),
    };
    let said = credentials(&remote);
    let message = "t0ken in https://alice:hunter2@example.test/base?key=s3cret#top";
    assert_eq!(
        kept(message, &said),
        "[credential] in https://[credential]@example.test/base?[credential]#top"
    );
    assert_eq!(kept(&"€".repeat(600), &said), "€".repeat(512));
    assert!(credentials(&Selection::File("/root".into())).is_empty());
}

#[test]
fn a_refusal_that_echoes_the_token_is_kept_clean_in_the_record_and_the_reason() {
    let scratch = Scratch::new("refusal-echo-block");
    let server =
        Server::new(|path, body| refusing(path, body, || refusal(403, "forbidden", &echoing())));
    let settings = queued(scratch.path(), &server.url, &["echoed"]);
    let Gated::Ran(run) = landing(&settings, scratch.path(), at(0)) else {
        panic!("ran")
    };
    clean(run.stopped.expect("blocked").message());
    let kept = record(&settings, scratch.path());
    clean(&kept.block.expect("a block").message);
    clean(&kept.cause.expect("a cause"));
    // A refusal that holds the turn: its reason file is kept clean too.
    let scratch = Scratch::new("refusal-echo-hold");
    let server =
        Server::new(|path, body| refusing(path, body, || refusal(422, "value_error", &echoing())));
    let settings = queued(scratch.path(), &server.url, &["held"]);
    landing(&settings, scratch.path(), at(0));
    let reason = settings.spool.join("held").join(ID).join("1.reason");
    let reason: Value = serde_json::from_str(&fs::read_to_string(reason).unwrap()).unwrap();
    clean(reason["message"].as_str().unwrap());
}

#[test]
fn a_handed_url_reaches_the_lander_in_its_environment_never_its_arguments() {
    let url = "http://alice:hunter2@127.0.0.1:9/?key=s3cret";
    let home = Path::new("/home");
    let handed = command(&Lander {
        home,
        spool: None,
        url: Some(url),
    })
    .unwrap();
    let words: Vec<_> = handed.get_args().map(OsStr::to_string_lossy).collect();
    assert!(
        words.iter().all(|word| !word.contains("hunter2")),
        "{words:?}"
    );
    let env = |command: &std::process::Command| {
        let found = command.get_envs().find(|(name, _)| *name == LANDER_URL);
        found.map(|(_, value)| value.map(OsStr::to_os_string))
    };
    assert_eq!(env(&handed), Some(Some(url.into())));
    let bare = command(&Lander {
        home,
        spool: None,
        url: None,
    })
    .unwrap();
    // One inherited from the hook is removed, so no stale URL is used.
    assert_eq!(env(&bare), Some(None));
}

/// The lander's clock: the start, and as far on as the server moved it.
static LATER: AtomicI64 = AtomicI64::new(0);

fn later() -> SystemTime {
    at(LATER.load(Ordering::SeqCst))
}

#[test]
fn a_wait_and_a_block_are_timed_from_when_the_refusal_arrived() {
    let no_sleep = |_: Duration| {};
    let replies: [(u16, &str, &str); 2] = [(429, "11", "wait"), (403, "", "block")];
    for (status, after, what) in replies {
        let scratch = Scratch::new("refusal-arrival");
        let server = Server::new(move |path, body| {
            if !is_part(path, body) {
                return path.ends_with("write_records").then(|| written(body, 1));
            }
            // The part takes 20 s to be answered.
            LATER.store(20, Ordering::SeqCst);
            match status {
                429 => Some((429, vec![("Retry-After", after.to_string())], json!({}))),
                _ => Some(refusal(403, "forbidden", "no writes")),
            }
        });
        let settings = queued(scratch.path(), &server.url, &[what]);
        LATER.store(0, Ordering::SeqCst);
        let (cap, patience) = (Cap::on(later), Patience::on(no_sleep));
        let here = Here {
            patience: Some(&patience),
            ..here(&settings, &cap)
        };
        land(&Queue::new(&settings.spool), ID, &here, scratch.path()).unwrap();
        let kept = record(&settings, scratch.path());
        let from = |seconds: i64| Some(micros_z(stamp(at(20 + seconds))));
        match status {
            429 => assert_eq!(kept.wait_until, from(11), "{what}"),
            _ => {
                let block = kept.block.unwrap();
                assert_eq!(Some(block.next_attempt), from(SIX_HOURS), "{what}");
                assert_eq!(Some(block.since), from(0), "{what}");
            }
        }
    }
}

#[test]
fn a_refused_probe_in_the_attempt_keeps_the_turn_and_blocks_again() {
    let scratch = Scratch::new("refusal-probe");
    let server = Server::new(|path, body| refusing(path, body, || refusal(403, "forbidden", "no")));
    let settings = queued(scratch.path(), &server.url, &["probed"]);
    let home = scratch.path();
    landing(&settings, home, at(0));
    let before = server.log.lock().unwrap().len();
    // Six hours on, the probe is refused in a way that holds a turn
    // outside the attempt; a part would be taken.
    server.answer(|path, body| {
        let probe = path.ends_with("write_records") && !is_part(path, body);
        match probe && body["discard"] != true {
            true => Some(refusal(422, "value_error", "refused probe")),
            false => path.ends_with("write_records").then(|| written(body, 1)),
        }
    });
    let Gated::Ran(run) = landing(&settings, home, at(SIX_HOURS)) else {
        panic!("the attempt ran")
    };
    assert!(matches!(
        run.stopped,
        Some(Stop::Block {
            block: Block::Forbidden,
            status: Some(422),
            ..
        })
    ));
    assert_eq!(Queue::new(&settings.spool).pending(ID).unwrap(), [1]);
    assert!(!settings.spool.join("held").exists());
    let log = server.log.lock().unwrap()[before..].to_vec();
    assert!(
        log.iter().all(|(_, body)| body["discard"] != true),
        "nothing discarded"
    );
    assert!(
        log.iter().all(|(path, body)| !is_part(path, body)),
        "no part was sent"
    );
    let block = record(&settings, home).block.unwrap();
    assert_eq!(
        Some(block.next_attempt),
        Some(micros_z(stamp(at(2 * SIX_HOURS))))
    );
}
