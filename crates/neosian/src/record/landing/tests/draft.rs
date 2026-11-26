//! Draft ids: the digest computed by hand, and no two turns sharing one.

use std::fs;

use super::super::draft::{draft_id, first_line, fresh_id, micros_z, opening_line};
use super::super::queue::Queue;
use super::super::state::State;
use super::{ID, spool, state};
use crate::conversation::turn_records::check_draft_id;
use crate::store::time::Stamp;
use crate::testing::Scratch;

/// 2026-11-26T12:00:00Z in microseconds since the epoch.
const NOON: i64 = 1_795_694_400_000_000;

#[test]
fn the_draft_id_is_the_digest_computed_by_hand() {
    let line = opening_line(
        Stamp::from_micros(NOON),
        "00000000-0000-4000-8000-000000000000",
    );
    assert_eq!(
        line,
        "{\"kind\": \"span\", \"opened_at\": \"2026-11-26T12:00:00.000000Z\", \"nonce\": \"00000000-0000-4000-8000-000000000000\"}"
    );
    // SHA-256 of "<id>\n<line>" and of "<id>\n<line>\n1", by Python's hashlib.
    assert_eq!(draft_id(ID, &line), "pcbdfd37c99da96bec91e19acc1321d28");
    assert_eq!(fresh_id(ID, &line), "pf17d1cc3eda128aeb5b066cef084c9d7");
    check_draft_id(&draft_id(ID, &line)).unwrap();
    check_draft_id(&fresh_id(ID, &line)).unwrap();
    assert_eq!(
        micros_z(Stamp::from_micros(NOON + 123_456)),
        "2026-11-26T12:00:00.123456Z"
    );
}

#[test]
fn two_turns_opened_by_the_same_prompt_get_different_ids() {
    let scratch = Scratch::new("landing-same-prompt");
    let queue = Queue::new(&spool(scratch.path()));
    let same =
        "{\"kind\": \"prompt\", \"text\": \"yes\"}\n{\"kind\": \"stop\", \"text\": \"Done.\"}\n";
    let mut drafts = Vec::new();
    let mut nonces = Vec::new();
    for _ in 0..2 {
        let span = spool(scratch.path()).join(format!("{ID}.jsonl"));
        fs::create_dir_all(spool(scratch.path())).unwrap();
        fs::write(&span, same).unwrap();
        let n = queue
            .close(&span, same.as_bytes(), ID, None, state, || Ok(()))
            .unwrap();
        let pending = queue.pending_dir(ID);
        let text = fs::read_to_string(Queue::part(&pending, n, "jsonl")).unwrap();
        let written = State::read(&Queue::part(&pending, n, "state")).unwrap();
        assert_eq!(written.draft, draft_id(ID, first_line(&text)));
        let opening: serde_json::Value = serde_json::from_str(first_line(&text)).unwrap();
        nonces.push(opening["nonce"].as_str().unwrap().to_string());
        drafts.push(written.draft);
        assert!(text.ends_with(same));
    }
    assert_ne!(nonces[0], nonces[1]);
    assert_ne!(drafts[0], drafts[1]);
}
