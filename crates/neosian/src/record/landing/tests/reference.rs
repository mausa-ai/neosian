//! The reference turn of the T1e1b independent check: a prompt, sixteen
//! tool rounds whose results hold 1,048,576 characters each, and a reply,
//! packed by `pack.rs`. The boundaries and the digest of the bodies below
//! were computed by a packer written apart from this code, from wire.md's
//! bounds and the encoder's key rules, over the records `neosian serve`
//! answered for the same span landed by the Stop hook on a records
//! FileStore (`record/t1e1b-2026-11-26/independent_pack.py`).

use serde_json::json;

use super::super::pack::{Draft, Parts, turn_bytes};
use super::{ID, record};
use crate::conversation::turn_records::records_of;
use crate::record::fingerprint::digest;
use crate::record::payload::{Record, messages_of};
use crate::remote::records::encode;

const DRAFT: &str = "p0123456789abcdef0123456789abcdef";

/// The span, as the spool keeps it.
fn span() -> Vec<Record> {
    let mut lines = vec![record(json!({"kind": "prompt", "text": "build it"}))];
    for round in 0..16 {
        let unit = format!("round {round} é 中 😀 \"quoted\" \\ \t\u{1}\n");
        let output: String = unit.repeat(1 << 20).chars().take(1 << 20).collect();
        lines.push(record(
            json!({"kind": "tool", "id": format!("call-{round}"),
            "name": "Bash", "input": {"command": format!("make part-{round}")},
            "response": output}),
        ));
    }
    lines.push(record(json!({"kind": "stop", "text": "built"})));
    lines
}

/// Each part's first `seq`, records and body bytes.
const PARTS: [(u64, usize, usize); 29] = [
    (0, 16, 919_284),
    (16, 16, 929_086),
    (32, 14, 918_913),
    (46, 16, 929_083),
    (62, 14, 918_916),
    (76, 16, 929_080),
    (92, 14, 918_925),
    (106, 16, 929_107),
    (122, 16, 929_120),
    (138, 14, 918_939),
    (152, 16, 929_117),
    (168, 14, 918_942),
    (182, 16, 929_114),
    (198, 14, 918_945),
    (212, 16, 929_111),
    (228, 14, 918_948),
    (242, 16, 929_111),
    (258, 16, 929_131),
    (274, 14, 919_010),
    (288, 16, 973_944),
    (304, 14, 919_014),
    (318, 16, 973_940),
    (334, 16, 973_933),
    (350, 14, 919_025),
    (364, 16, 973_946),
    (380, 14, 919_012),
    (394, 16, 973_942),
    (410, 14, 919_016),
    (424, 4, 186_240),
];
const BODIES_SHA256: &str = "b4c81ea4068fe680a2a246b81c93cb5e52913d340fa855f1209a2a6d98bb44e5";

#[test]
fn the_reference_turn_packs_as_the_independent_packer_does() {
    let records = records_of(&messages_of(&span())).unwrap();
    let actor = format!("claude-code:{ID}");
    let draft = Draft {
        conversation: ID,
        draft: DRAFT,
        actor: &actor,
        turn_bytes: turn_bytes(&records),
    };
    let mut bodies = Vec::new();
    let mut found = Vec::new();
    for part in Parts::new(&draft, records, true) {
        let write = part.unwrap();
        let body = encode::write(ID, &write);
        found.push((write.records[0].seq, write.records.len(), body.len()));
        bodies.extend_from_slice(body.as_bytes());
    }
    assert_eq!(draft.turn_bytes, 20_326_263);
    assert_eq!(found, PARTS);
    assert_eq!(digest(&bodies), BODIES_SHA256);
}
