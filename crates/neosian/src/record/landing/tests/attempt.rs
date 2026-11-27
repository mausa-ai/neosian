//! A server's answers about the whole destination (DESIGN §40.54 items
//! 17.3 to 17.5 and 23; owner decision O10), over HTTP against `http.rs`
//! with an injected clock and sleep: a `Retry-After` of at most 10 s is
//! waited within 30 s a run and then written as a wait; a block lets
//! nothing through for six hours, then one part whose answer decides; a
//! handshake that refuses the credential blocks as a 401 does.

use std::cell::RefCell;
use std::collections::VecDeque;
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{Value, json};

use super::super::cap::{Cap, Patience};
use super::super::destination::{SIX_HOURS, key, read};
use super::super::gate::{Gated, land};
use super::super::queue::Queue;
use super::super::rules::{Block, Stop};
use super::super::slots::directory;
use super::super::state::describe;
use super::http::{Server, refusal, written};
use super::land::turn;
use super::{ID, settings};
use crate::record::land::here;
use crate::record::settings::Settings;
use crate::remote::selection::Selection;
use crate::testing::Scratch;

thread_local! {
    static SLEPT: RefCell<Vec<u64>> = const { RefCell::new(Vec::new()) };
}

fn sleep(duration: Duration) {
    SLEPT.with(|slept| slept.borrow_mut().push(duration.as_secs()));
}

fn at(seconds: i64) -> SystemTime {
    UNIX_EPOCH + Duration::from_secs(1_795_000_000 + seconds.unsigned_abs())
}

fn is_part(path: &str, body: &Value) -> bool {
    path.ends_with("write_records") && body["records"].as_array().is_some_and(|r| !r.is_empty())
}

/// `prompts` closed for a server at `url` and queued, the conversation's
/// lock held meanwhile so that nothing lands.
fn queued(base: &std::path::Path, url: &str, prompts: &[&str]) -> Settings {
    let settings = Settings {
        store: Selection::Remote {
            url: url.into(),
            token: "test-token".into(),
        },
        ..settings(base, None)
    };
    let queue = Queue::new(&settings.spool);
    let lock = queue.try_lock(ID).unwrap().unwrap();
    for prompt in prompts {
        assert_eq!(turn(&settings, prompt).unwrap().disposition, "pending");
    }
    drop(lock);
    settings
}

/// A server that answers each part with `part` and each probe as a store.
fn refusing(
    path: &str,
    body: &Value,
    part: fn() -> super::http::Reply,
) -> Option<super::http::Reply> {
    match is_part(path, body) {
        true => Some(part()),
        false => path.ends_with("write_records").then(|| written(body, 1)),
    }
}

/// One lander's landing of the conversation at `now`, waiting on `sleep`.
fn landing(settings: &Settings, home: &std::path::Path, now: SystemTime) -> Gated {
    let patience = Patience::on(sleep);
    let here = here(settings, &Cap::NONE);
    let here = super::super::turn::Here {
        patience: Some(&patience),
        ..here
    };
    land(&Queue::new(&settings.spool), ID, &here, home, now).unwrap()
}

fn stopped(gated: Gated) -> Option<Stop> {
    match gated {
        Gated::Ran(run) => run.stopped,
        Gated::Closed => panic!("the destination was asked"),
        Gated::Busy => panic!("the conversation was free"),
    }
}

#[test]
fn a_retry_after_of_at_most_10_s_is_waited_within_30_s_a_run_then_written_as_a_wait() {
    for (seconds, waited, written_as) in [
        (vec![1], vec![1], None),
        (vec![10, 10, 10, 11], vec![10, 10, 10], Some(11)),
        (vec![10, 10, 10, 10], vec![10, 10, 10], Some(10)),
    ] {
        SLEPT.with(|slept| slept.borrow_mut().clear());
        let scratch = Scratch::new("attempt-retry");
        let left = Mutex::new(seconds.iter().copied().collect::<VecDeque<_>>());
        let server = Server::new(move |path, body| {
            if !path.ends_with("write_records") {
                return None;
            }
            let next = || left.lock().unwrap().pop_front();
            match is_part(path, body).then(next).flatten() {
                Some(after) => Some((429, vec![("Retry-After", after.to_string())], json!({}))),
                None => Some(written(body, 1)),
            }
        });
        let settings = queued(scratch.path(), &server.url, &["throttled"]);
        let stop = stopped(landing(&settings, scratch.path(), at(0)));
        assert_eq!(SLEPT.with(|slept| slept.borrow().clone()), waited);
        let record = read(&directory(scratch.path()), &key(&describe(&settings.store)));
        let pending = Queue::new(&settings.spool).pending(ID).unwrap();
        match written_as {
            None => {
                assert!(stop.is_none() && record.is_none() && pending.is_empty());
                assert_eq!(server.parts().len(), 2, "the part, then the part again");
            }
            Some(after) => {
                let wait = Stop::Wait {
                    seconds: after,
                    failing: false,
                    message: "the destination answered 429: {}".into(),
                };
                assert_eq!(stop, Some(wait));
                let until = super::super::destination::stamp(at(after as i64));
                let until = super::super::draft::micros_z(until);
                assert_eq!(record.unwrap().wait_until, Some(until));
                assert_eq!(pending, [1]);
            }
        }
    }
}

#[test]
fn a_blocked_destination_gets_one_part_six_hours_on_and_its_answer_decides() {
    let scratch = Scratch::new("attempt-six-hours");
    let server =
        Server::new(|path, body| refusing(path, body, || refusal(403, "forbidden", "no writes")));
    let settings = queued(scratch.path(), &server.url, &["one", "two"]);
    let (home, queue) = (scratch.path(), Queue::new(&settings.spool));
    let record = || read(&directory(home), &key(&describe(&settings.store)));
    let stop = stopped(landing(&settings, home, at(0)));
    assert!(matches!(
        stop,
        Some(Stop::Block {
            block: Block::Forbidden,
            ..
        })
    ));
    let blocked = record().unwrap().block.unwrap();
    assert_eq!(
        (blocked.status, blocked.message.as_str()),
        (Some(403), "[forbidden] no writes")
    );
    let asked = server.log.lock().unwrap().len();
    // 5 h 59 min: nothing is asked.
    assert!(matches!(
        landing(&settings, home, at(SIX_HOURS - 60)),
        Gated::Closed
    ));
    assert_eq!(server.log.lock().unwrap().len(), asked);
    // 6 h: one part. Refused (here with an answer that would hold the turn
    // outside the attempt), the turn stays as it was and the six hours
    // start over.
    server.answer(|path, body| refusing(path, body, || refusal(400, "value_error", "bad")));
    let stop = stopped(landing(&settings, home, at(SIX_HOURS)));
    assert!(matches!(
        stop,
        Some(Stop::Block {
            block: Block::Forbidden,
            ..
        })
    ));
    assert_eq!(server.parts().len(), 2);
    assert_eq!(queue.pending(ID).unwrap(), [1, 2]);
    assert!(!settings.spool.join("held").exists());
    let again = record().unwrap().block.unwrap();
    assert_eq!(
        (again.since.as_str(), again.status),
        (blocked.since.as_str(), Some(400))
    );
    assert!(matches!(
        landing(&settings, home, at(2 * SIX_HOURS - 1)),
        Gated::Closed
    ));
    // Taken: unblocked before the run goes on, and the queue lands.
    let turns = Mutex::new(0);
    server.answer(move |path, body| {
        let mut turns = turns.lock().unwrap();
        *turns += u64::from(body["seal"] == true);
        path.ends_with("write_records")
            .then(|| written(body, *turns))
    });
    assert_eq!(stopped(landing(&settings, home, at(2 * SIX_HOURS))), None);
    assert_eq!(server.parts().len(), 4);
    assert!(queue.pending(ID).unwrap().is_empty());
    assert_eq!(record(), None);
}

#[test]
fn a_handshake_that_refuses_the_credential_blocks_it_and_a_fault_waits() {
    for (status, credential) in [(401, true), (503, false)] {
        let scratch = Scratch::new("attempt-handshake");
        let server = Server::new(move |path, _| {
            (path == "/v1/capabilities").then(|| (status, vec![], json!({})))
        });
        let settings = queued(scratch.path(), &server.url, &["refused"]);
        let stop = stopped(landing(&settings, scratch.path(), at(0))).unwrap();
        let record = read(&directory(scratch.path()), &key(&describe(&settings.store))).unwrap();
        match credential {
            true => {
                assert!(matches!(
                    stop,
                    Stop::Block {
                        block: Block::Credential,
                        ..
                    }
                ));
                let block = record.block.unwrap();
                assert_eq!(block.reason, "credential");
                assert!(
                    block.message.contains("rejected the bearer token"),
                    "{}",
                    block.message
                );
            }
            false => {
                assert!(matches!(stop, Stop::Wait { failing: true, .. }));
                assert!(record.block.is_none() && record.failing_since.is_some());
            }
        }
        assert!(server.parts().is_empty());
    }
}
