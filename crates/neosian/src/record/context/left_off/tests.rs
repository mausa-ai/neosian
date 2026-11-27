//! "Where we left off" from light records against the released block: the
//! handoff's parity corpus, migrated to records on a FileStore, gives the
//! plain store's block byte for byte at several budgets and after a
//! compaction; a conversation of large results is read by heads through a
//! fixed turn; reasoning, extra data, attachment bodies and an erased
//! turn's text never enter the block.

use serde_json::json;

use super::{History, render_left_off};
use crate::conversation::handoff::tests::Counted;
use crate::conversation::handoff::tests::corpus::{calls, calls_as_lines, folded, sized, turn};
use crate::conversation::turn_records::Kind;
use crate::conversation::{Projection, Turn};
use crate::limits;
use crate::state::State;
use crate::state::records::Source;
use crate::store::FileStore;
use crate::store::grammar::Actor;
use crate::testing::Scratch;

const SCOPE: &str = "user:left";

type Conversation = (&'static str, Vec<Turn>, Vec<Projection>);

/// A FileStore holding `conversations` as records and a plain one holding
/// them whole, each with a sessions document per conversation written in
/// order, the last one newest.
struct Stores {
    kept: Source,
    plain: FileStore,
    _dirs: (Scratch, Scratch),
}

fn stores(name: &str, conversations: &[Conversation]) -> Stores {
    let (kept_dir, plain_dir) = (
        Scratch::new(&format!("{name}-kept")),
        Scratch::new(&format!("{name}-plain")),
    );
    let kept = FileStore::open(kept_dir.path()).unwrap();
    kept.enable_records().unwrap();
    let plain = FileStore::open(plain_dir.path()).unwrap();
    let actor = Actor::parse("codex:1").unwrap();
    for (id, turns, entries) in conversations {
        kept.restore_conversation(id, turns, entries, &[]).unwrap();
        let migrated = kept.migrate_records(id, &limits::Records::load().unwrap());
        assert!(migrated.unwrap().migrated > 0, "{id}");
        plain.restore_conversation(id, turns, entries, &[]).unwrap();
        let listing = format!("# codex session {id}\n\n- turns: {}\n", turns.len());
        for store in [&kept as &dyn State, &plain] {
            let path = format!("sessions/{id}");
            store.write(SCOPE, &path, &listing, &actor, None).unwrap();
        }
    }
    Stores {
        kept: Source::File(kept),
        plain,
        _dirs: (kept_dir, plain_dir),
    }
}

fn left(history: History, own: &str, source: &str, budget: isize) -> String {
    render_left_off(history, SCOPE, own, source, budget).unwrap()
}

fn corpus() -> Vec<Conversation> {
    let (fold, entries) = folded("c-fold");
    vec![
        ("c-calls", calls("c-calls"), Vec::new()),
        ("c-lines", calls_as_lines("c-lines"), Vec::new()),
        ("c-fold", fold, entries),
        ("c-many", sized("c-many", 40, 1_200), Vec::new()),
        ("c-share", sized("c-share", 6, 5_000), Vec::new()),
        ("c-newest", sized("c-newest", 3, 30_000), Vec::new()),
    ]
}

#[test]
fn the_block_from_light_records_is_the_released_block() {
    let stores = stores("left-parity", &corpus());
    let keeper = stores.kept.keeper().unwrap().unwrap();
    let kept = History {
        state: stores.kept.state(),
        records: Some(&keeper),
    };
    let plain = History {
        state: &stores.plain,
        records: None,
    };
    for budget in [8_192, 6_000, 2_500, 900, 1] {
        let released = left(plain, "", "startup", budget);
        assert_eq!(left(kept, "", "startup", budget), released, "{budget}");
    }
    let released = left(plain, "", "startup", 8_192);
    assert!(released.contains("[conversation c-newest — written by claude-code:8]"));
    assert!(released.contains("[conversation c-many — written by claude-code:7]"));
    for (id, _, _) in corpus() {
        for budget in [6_000, 700] {
            let released = left(plain, id, "compact", budget);
            assert_eq!(left(kept, id, "compact", budget), released, "{id} {budget}");
        }
    }
    let fold = left(plain, "c-fold", "compact", 6_000);
    let shown = [
        "\n[1-34] 34 earlier turns not shown\n[35] USER: prompt 35 [link c-fold:1] word",
        "\n[39] the second newest, shown verbatim instead\n[40] USER: prompt 40",
    ];
    assert!(shown.iter().all(|line| fold.contains(line)), "{fold}");
    let calls = left(plain, "c-calls", "compact", 6_000);
    assert!(
        calls.contains("error: no match in [link c-calls:2]"),
        "{calls}"
    );
}

/// `turns` turns, each a tool round whose result holds `half` bytes of
/// one letter and then `half` of another, between three canaries.
fn big(id: &'static str, turns: u64, half: usize) -> Conversation {
    let result = |n: u64| {
        let (z, y) = ("z ".repeat(half / 2), "y ".repeat(half / 2));
        format!("HEAD-{n} {z} MIDDLE-{n} {y} TAIL-{n}")
    };
    let all = (1..=turns)
        .map(|n| {
            turn(id, n, &[
                json!({"role": "user", "content": format!("step {n}")}),
                json!({"role": "assistant", "content": null, "tool_calls": [{"id": "r", "name": "Bash", "arguments": {"command": "cat log"}}]}),
                json!({"role": "tool", "content": result(n), "tool_call_id": "r"}),
                json!({"role": "assistant", "content": format!("done {n}")}),
            ])
        })
        .collect();
    (id, all, Vec::new())
}

#[test]
fn a_session_of_large_results_is_read_by_heads_from_its_newest_turns() {
    // Six turns of 1.5 MiB: the 4 MiB window reads the newest two.
    let stores = stores("left-bounds", &[big("c-big", 6, 3 << 18)]);
    let keeper = stores.kept.keeper().unwrap().unwrap();
    let counted = Counted::new(&keeper);
    let kept = History {
        state: stores.kept.state(),
        records: Some(&counted),
    };
    let text = left(kept, "", "startup", 6_000);
    let digest = |n: u64| format!("TOOL Bash({{\"command\":\"cat log\"}}) → HEAD-{n} z z");
    let shown = format!(
        "\n[1-4] 4 earlier turns not shown\n[5] USER: step 5 | {}",
        digest(5)
    );
    assert!(text.contains(&shown), "{text}");
    assert!(text.contains("TAIL-6 | AGENT: done 6\n"), "{text}");
    assert!(
        !text.contains("MIDDLE-") && !text.contains("step 4"),
        "{text}"
    );
    let asked = counted.asked.borrow();
    assert_eq!(asked.len(), 2, "one header read, one view read");
    assert_eq!(asked[0].kinds, Some(Vec::new()));
    assert_eq!((asked[1].after, asked[1].through), (4, Some(6)));
    assert_eq!(asked[1].head(Kind::ToolResult), Some(8_192));
    for read in asked.iter() {
        let kinds = read.kinds.as_deref().unwrap_or_default();
        assert!(!kinds.contains(&Kind::Reasoning) && !kinds.contains(&Kind::Extra));
    }
    // Nine MiB of results are stored; heads of 8 KiB each side of two are read.
    let bytes = counted.bytes.get();
    assert!(bytes < 2 * 2 * 8_192 + 4_096, "{bytes}");
}

#[test]
fn a_newest_turn_past_the_window_is_folded_unread() {
    let stores = stores("left-huge", &[big("c-huge", 2, 5 << 20)]);
    let keeper = stores.kept.keeper().unwrap().unwrap();
    let counted = Counted::new(&keeper);
    let kept = History {
        state: stores.kept.state(),
        records: Some(&counted),
    };
    let text = left(kept, "", "startup", 6_000);
    let block =
        "[conversation c-huge — written by claude-code:7]\n[1-2] 2 earlier turns not shown\n";
    assert!(text.contains(block), "{text}");
    assert_eq!(counted.asked.borrow().len(), 1, "the headers alone");
    assert_eq!(counted.bytes.get(), 0);
}

#[test]
fn private_kinds_and_an_erased_turn_stay_out_of_the_block() {
    let id = "c-private";
    let turns = vec![
        turn(
            id,
            1,
            &[
                json!({"role": "user", "content": [{"type": "text", "text": "see"}, {"type": "image", "media_type": "image/png", "data": "ATTACHMENT-CANARY"}]}),
                json!({"role": "assistant", "content": "ok", "reasoning": {"thought": "REASONING-CANARY"}, "extra": {"note": "EXTRA-CANARY"}}),
            ],
        ),
        turn(
            id,
            2,
            &[
                json!({"role": "user", "content": "the key is ERASED-CANARY"}),
                json!({"role": "assistant", "content": "noted"}),
            ],
        ),
    ];
    let stores = stores("left-private", &[(id, turns, Vec::new())]);
    for store in [stores.kept.state(), &stores.plain as &dyn State] {
        assert_eq!(store.redact_turns(id, None, Some(&[2]), None).unwrap(), 1);
    }
    let keeper = stores.kept.keeper().unwrap().unwrap();
    let kept = History {
        state: stores.kept.state(),
        records: Some(&keeper),
    };
    let text = left(kept, "", "startup", 6_000);
    assert!(
        text.contains("\n[1] USER: see | AGENT: ok\n[2] [redacted]\n"),
        "{text}"
    );
    assert!(!text.contains("CANARY"), "{text}");
    let plain = History {
        state: &stores.plain,
        records: None,
    };
    assert_eq!(text, left(plain, "", "startup", 6_000));
}
