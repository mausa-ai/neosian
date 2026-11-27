//! The rows of 17.4 and the answers of D10, one case each (DESIGN §40.54
//! item 17.4; rulings D10 and E8 of the T1e1 plan): the table is pure, so
//! each answer is built here as a server or a local store sends it and the
//! action compared whole.

use serde_json::{Value, json};

use super::super::answer::Answer;
use super::super::rules::{Action, Block, Route, Stop, decide};
use super::super::state::Reason;
use crate::conversation::turn_records::{Header, State as DraftState, Written};
use crate::remote::records::Refusal;
use crate::store::time::Stamp;

const CONFLICT: &str = "agent_conversation_conflict";

fn refusal(status: u16, code: Option<&str>, message: &str, details: Value) -> Answer {
    Answer::Refused(Refusal {
        status,
        retry_after: None,
        code: code.map(str::to_string),
        message: message.into(),
        details,
    })
}

fn conflict(reason: &str, details: Value) -> Answer {
    let mut details = details;
    details["reason"] = json!(reason);
    let message = format!("Conversation conflict on 'c': {reason}");
    refusal(400, Some(CONFLICT), &message, details)
}

fn throttled(seconds: Option<u64>) -> Answer {
    Answer::Refused(Refusal {
        status: 429,
        retry_after: seconds,
        code: None,
        message: String::new(),
        details: Value::Null,
    })
}

fn header() -> Header {
    Header {
        turn: 7,
        created_at: Stamp::parse("2026-11-26T12:00:00.5Z").unwrap(),
        actor: Some("client:c/claude-code:s".into()),
        redacted: false,
        records: 3,
        bytes: 12,
    }
}

fn written(state: DraftState, next_seq: u64, turn: Option<Header>) -> Answer {
    Answer::Written(Written {
        draft: "p0".into(),
        state,
        next_seq,
        turn,
        stored_bytes: 0,
        freed_bytes: 0,
    })
}

fn held(reason: &'static str, status: u16, code: Option<&str>, message: &str) -> Reason {
    let code = code.map(str::to_string);
    Reason::answered(reason, Some(status), code, message.into())
}

fn records(answer: &Answer) -> Action {
    decide(Route::Records, answer)
}

/// A block's kind and message; its status and code are the refusal's.
fn kind(action: Action) -> Option<(Block, String)> {
    match action {
        Action::Keep(Stop::Block { block, message, .. }) => Some((block, message)),
        _ => None,
    }
}

fn wait(seconds: u64, failing: bool, message: &str) -> Action {
    Action::Keep(Stop::Wait {
        seconds,
        failing,
        message: message.into(),
    })
}

#[test]
fn a_200_records_the_draft_s_place_or_stores_the_turn() {
    let open = written(DraftState::Open, 9, None);
    assert_eq!(records(&open), Action::Took(DraftState::Open, 9));
    let none = written(DraftState::None, 0, None);
    assert_eq!(records(&none), Action::Took(DraftState::None, 0));
    let sealed = written(DraftState::Sealed, 3, Some(header()));
    assert_eq!(records(&sealed), Action::Stored(header()));
    let headless = written(DraftState::Sealed, 3, None);
    let expected = wait(60, true, "draft p0 answered sealed without its turn");
    assert_eq!(records(&headless), expected);
}

#[test]
fn a_429_waits_in_the_run_up_to_10_seconds_and_makes_the_destination_wait_beyond() {
    let short = Stop::Retry {
        seconds: 10,
        message: "the destination answered 429".into(),
    };
    assert_eq!(records(&throttled(Some(10))), Action::Keep(short));
    let long = wait(11, false, "the destination answered 429");
    assert_eq!(records(&throttled(Some(11))), long);
    let absent = wait(60, false, "the destination answered 429");
    assert_eq!(records(&throttled(None)), absent);
    let three = throttled(Some(3));
    assert_eq!(decide(Route::Whole, &three), records(&three));
}

#[test]
fn a_full_account_a_refusal_of_writes_and_a_refused_credential_block_the_destination() {
    let full = refusal(413, Some("storage_exceeded"), "full", json!({}));
    let refused = refusal(403, Some("forbidden"), "no", json!({}));
    for (answer, block, message) in [
        (full, Block::Storage, "[storage_exceeded] full"),
        (refused, Block::Forbidden, "[forbidden] no"),
        (
            refusal(302, None, "", Value::Null),
            Block::Forbidden,
            "the destination answered 302",
        ),
        (
            refusal(401, None, "", Value::Null),
            Block::Credential,
            "the destination answered 401",
        ),
    ] {
        let expected = Some((block, message.to_string()));
        assert_eq!(kind(records(&answer)), expected);
        assert_eq!(kind(decide(Route::Whole, &answer)), expected);
    }
}

#[test]
fn a_refused_value_holds_the_turn_with_the_message() {
    let limit = "resource limit NEOSIAN_MAX_RECORD_TURN_BYTES exceeded: 9 > 8";
    for (status, code, message) in [
        (400, Some("value_error"), "record 3: bad"),
        (413, Some("value_error"), limit),
        (422, Some("anything"), "unprocessable"),
    ] {
        let answer = refusal(status, code, message, json!({}));
        let expected = Action::Hold(held("refused", status, code, message));
        assert_eq!(records(&answer), expected);
        assert_eq!(decide(Route::Whole, &answer), expected);
    }
    // A gateway's 422 without a body is held with what the person is told.
    let bare = refusal(422, None, "", Value::Null);
    let told = "the destination answered 422";
    assert_eq!(
        records(&bare),
        Action::Hold(held("refused", 422, None, told))
    );
}

#[test]
fn a_draft_that_does_not_match_is_written_again_under_the_fresh_id() {
    for reason in ["draft_mismatch", "draft_size"] {
        let message = format!("Conversation conflict on 'c': {reason}");
        let expected = held("mismatch", 400, Some(CONFLICT), &message);
        let answer = conflict(reason, json!({"next_seq": 1}));
        assert_eq!(records(&answer), Action::Fresh(expected));
    }
}

#[test]
fn a_sealed_draft_is_the_stored_turn() {
    let carried = conflict("draft_sealed", json!({"turn": header().to_json()}));
    assert_eq!(records(&carried), Action::Stored(header()));
    assert_eq!(records(&conflict("draft_sealed", json!({}))), Action::Probe);
}

#[test]
fn an_expired_draft_starts_again_and_open_drafts_stop_the_conversation() {
    assert_eq!(
        records(&conflict("draft_gap", json!({"next_seq": 0}))),
        Action::Restart
    );
    assert_eq!(
        records(&refusal(410, None, "", Value::Null)),
        Action::Restart
    );
    let open = conflict("drafts_open", json!({"limit": 16}));
    let message = "[agent_conversation_conflict] Conversation conflict on 'c': drafts_open";
    assert_eq!(
        records(&open),
        Action::Keep(Stop::Conversation(message.into()))
    );
    let actor = conflict("draft_actor", json!({"draft": "p0"}));
    let message = "Conversation conflict on 'c': draft_actor";
    let expected = held("actor_changed", 400, Some(CONFLICT), message);
    assert_eq!(records(&actor), Action::HoldAside(expected));
}

#[test]
fn faults_make_the_destination_wait_and_a_credential_that_cannot_be_read_blocks() {
    let setting = "NEOSIAN_MAX_RESPONSE_BYTES must be an integer in 1..1073741824";
    for (answer, told) in [
        (
            refusal(503, None, "", Value::Null),
            "the destination answered 503",
        ),
        (
            Answer::Lost("reset".into()),
            "no answer from the destination: reset",
        ),
        (Answer::Unreadable("bad".into()), "bad"),
        (
            Answer::Unasked {
                credential: false,
                message: setting.into(),
            },
            setting,
        ),
    ] {
        assert_eq!(records(&answer), wait(60, true, told));
    }
    let signed_out = "machine disconnected; run neosian login";
    let credential = Answer::Unasked {
        credential: true,
        message: signed_out.into(),
    };
    let blocked = Some((Block::Credential, signed_out.to_string()));
    assert_eq!(kind(records(&credential)), blocked);
}

#[test]
fn a_server_without_records_takes_the_turn_whole() {
    assert_eq!(records(&Answer::Unsupported), Action::Whole);
    let missing = refusal(404, None, "", Value::Null);
    assert_eq!(records(&missing), Action::Whole);
    // On `append_turn` a 404 is the destination's, blocked as a 3xx (E15).
    let blocked = Some((Block::Forbidden, "the destination answered 404".into()));
    assert_eq!(kind(decide(Route::Whole, &missing)), blocked);
}

#[test]
fn answers_17_4_does_not_name_follow_d10_as_e15_rules() {
    const CONFIG: &str = "agent_configuration_error";
    let configuration = refusal(400, Some(CONFIG), "no records", json!({}));
    let told = "[agent_configuration_error] no records";
    assert_eq!(records(&configuration), Action::Reread(told.into()));
    let reason = held("refused", 400, Some(CONFIG), "no records");
    assert_eq!(decide(Route::Whole, &configuration), Action::Hold(reason));
    // A conflict's reason counts only under the conflict code (R40).
    let unreadable = json!({"reason": "draft_mismatch"});
    let format = "agent_conversation_format_unsupported";
    for answer in [
        refusal(400, Some("agent_conversation_id_invalid"), "id", json!({})),
        refusal(409, None, "conflict", Value::Null),
        conflict("restore_open", json!({})),
        refusal(400, Some(format), "x", unreadable),
    ] {
        assert!(matches!(records(&answer), Action::Hold(_)), "{answer:?}");
    }
    let odd = refusal(600, None, "", Value::Null);
    assert_eq!(
        records(&odd),
        wait(60, true, "the destination answered 600")
    );
    // Only a conversation's own stop lets the run go on to its destination.
    assert!(!Stop::Conversation("x".into()).destination());
    assert!(matches!(records(&throttled(None)), Action::Keep(stop) if stop.destination()));
}

#[test]
fn a_format_refusal_of_the_turn_s_own_draft_gets_the_fresh_id_one_of_the_conversation_holds() {
    let format = "agent_conversation_format_unsupported";
    for own in [
        "a draft's state is unreadable",
        "a sealed draft's header is unreadable",
        "record 3: its digest is unreadable",
        "records line 2 is unreadable",
        "the requested range of stored records is unreadable",
    ] {
        let message = format!("Unsupported turn format in conversation 'c': {own}");
        let answer = refusal(400, Some(format), &message, json!({"reason": own}));
        let expected = held("mismatch", 400, Some(format), &message);
        assert_eq!(records(&answer), Action::Fresh(expected), "{own}");
        assert!(matches!(decide(Route::Whole, &answer), Action::Hold(_)));
    }
    let stored = "turn 3: a stored line is unreadable";
    let message = format!("Unsupported turn format in conversation 'c': {stored}");
    let answer = refusal(400, Some(format), &message, json!({"reason": stored}));
    let expected = held("refused", 400, Some(format), &message);
    assert_eq!(records(&answer), Action::Hold(expected));
}
