//! What the records path reads and shows at its bounds: a fixed last turn
//! and heads, never a whole conversation; `full` within the budget with
//! an oversized newest turn; reasoning and attachment bodies never shown;
//! the documented exceptions where heads cannot give the released bytes;
//! and a turn records cannot carry read around and shown as one line,
//! while a store without records shows its conversation as released.

use serde_json::json;

use super::corpus::{URL, turn, uncarried};
use super::{Conversation, Counted, light, shown, stores};
use crate::conversation::recall::View;
use crate::conversation::turn_records::Kind;

fn big(id: &'static str, turns: u64) -> Conversation {
    let result = |n: u64| {
        format!(
            "HEAD-{n} {} MIDDLE-{n} {} TAIL-{n}",
            "z ".repeat(1 << 20),
            "y ".repeat(1 << 20)
        )
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
fn a_conversation_of_large_results_is_read_by_heads_through_a_fixed_turn() {
    let stores = stores("handoff-bounds", &[big("c-big", 6)]);
    let keeper = stores.kept.keeper().unwrap().unwrap();
    let counted = Counted::new(&keeper);
    let state = stores.kept.state();
    let text = shown(state, Some(&counted), "c-big", View::Light);
    assert_eq!(text, shown(state, None, "c-big", View::Light));
    assert!(text.contains("HEAD-6") && text.contains("TAIL-6") && !text.contains("MIDDLE-"));
    let asked = counted.asked.borrow();
    assert_eq!(
        asked.len(),
        1,
        "one view read, through the last turn numbered"
    );
    assert_eq!(asked[0].through, Some(6));
    assert_eq!(asked[0].head(Kind::ToolResult), Some(8_192));
    for read in asked.iter() {
        let kinds = read.kinds.as_deref().unwrap_or_default();
        assert!(!kinds.contains(&Kind::Reasoning));
        assert_eq!(read.head(Kind::Extra), Some(1), "extra data holds no body");
    }
    // Twelve MiB of results are stored; heads of 8 KiB each side are read.
    assert!(
        counted.bytes.get() < 6 * 2 * 8_192 + 4_096,
        "{}",
        counted.bytes.get()
    );
}

#[test]
fn full_stays_within_the_budget_with_an_oversized_newest_turn() {
    let calls: Vec<_> = (0..400)
        .map(|n| json!({"id": format!("c{n}"), "name": "Read", "arguments": {"path": format!("/w/{n}/{}", "p".repeat(900))}}))
        .collect();
    let mut messages = vec![
        json!({"role": "user", "content": "x".repeat(20_000)}),
        json!({"role": "assistant", "content": "Reading.", "tool_calls": calls}),
    ];
    for n in 0..400 {
        messages.push(
            json!({"role": "tool", "content": "r".repeat(3_000), "tool_call_id": format!("c{n}")}),
        );
    }
    let id = "c-wide";
    let turns = vec![
        turn(id, 1, &[json!({"role": "user", "content": "first"})]),
        turn(id, 2, &messages),
    ];
    let stores = stores("handoff-full", &[(id, turns, Vec::new())]);
    let keeper = stores.kept.keeper().unwrap().unwrap();
    for view in [View::Full, View::Calls, View::Prompts] {
        let text = shown(stores.kept.state(), Some(&keeper), id, view);
        assert!(
            text.chars().count() <= 32_768,
            "{view:?} {}",
            text.chars().count()
        );
        assert_eq!(text, shown(&stores.plain, None, id, view));
    }
    let full = shown(stores.kept.state(), Some(&keeper), id, View::Full);
    assert!(full.contains("[1] USER: first"), "the older turn is a line");
    let more = "[turn 2 goes on past this view's budget; recall_turn(2, conversation=\"c-wide\") shows it]\n[end — ";
    assert!(full.contains(more), "{full}");
    let released = shown(stores.kept.state(), None, id, View::Light);
    assert!(
        released.chars().count() > 32_768,
        "the default view keeps the newest whole"
    );
}

#[test]
fn reasoning_and_attachment_bodies_stay_out_of_every_view() {
    let id = "c-private";
    let turns = vec![turn(
        id,
        1,
        &[
            json!({"role": "user", "content": [{"type": "text", "text": "see"}, {"type": "image", "media_type": "image/png", "data": "ATTACHMENT-CANARY"}]}),
            json!({"role": "assistant", "content": "ok", "reasoning": {"thought": "REASONING-CANARY"}, "extra": {"note": "EXTRA-CANARY"}}),
        ],
    )];
    let stores = stores("handoff-private", &[(id, turns, Vec::new())]);
    let keeper = stores.kept.keeper().unwrap().unwrap();
    for view in [View::Light, View::Prompts, View::Calls, View::Full] {
        let text = shown(stores.kept.state(), Some(&keeper), id, view);
        assert!(!text.contains("CANARY"), "{view:?}: {text}");
    }
}

/// A turn whose prompt is `chars` long, so the turns before it are lines.
fn wide(id: &str, number: u64, chars: usize) -> crate::conversation::Turn {
    turn(
        id,
        number,
        &[json!({"role": "user", "content": format!("big {}", "q ".repeat(chars / 2))})],
    )
}

#[test]
fn heads_differ_from_the_released_bytes_only_where_documented() {
    // A link first seen in an omitted middle: the next one takes its number.
    let hidden = format!("{URL}/only-in-the-middle-of-a-long-result");
    let seen = format!("{URL}/in-the-tail-the-read-keeps");
    let long = format!(
        "start {} {hidden} {} {seen} end",
        "a ".repeat(20_000),
        "b ".repeat(20_000)
    );
    let links = vec![
        turn(
            "c-hidden",
            1,
            &[
                json!({"role": "user", "content": "go"}),
                json!({"role": "assistant", "content": null, "tool_calls": [{"id": "a", "name": "Bash", "arguments": {}}]}),
                json!({"role": "tool", "content": long, "tool_call_id": "a"}),
            ],
        ),
        turn(
            "c-hidden",
            2,
            &[json!({"role": "user", "content": format!("open {hidden} {seen}")})],
        ),
        wide("c-hidden", 3, 24_560),
    ];
    // A long envelope whose head and tail join into other data.
    let envelope = json!({"success": true, "data": "A".repeat(9_000), "note": "B".repeat(9_000)});
    let structured = vec![turn(
        "c-structured",
        1,
        &[
            json!({"role": "assistant", "content": null, "tool_calls": [{"id": "e", "name": "Read", "arguments": {}}]}),
            json!({"role": "tool", "content": envelope.to_string(), "tool_call_id": "e"}),
        ],
    )];
    let oversized = vec![
        turn(
            "c-oversized",
            1,
            &[json!({"role": "user", "content": "first"})],
        ),
        wide("c-oversized", 2, 120_000),
    ];
    let stores = stores(
        "handoff-exceptions",
        &[
            ("c-hidden", links, Vec::new()),
            ("c-structured", structured, Vec::new()),
            ("c-oversized", oversized, Vec::new()),
        ],
    );
    let (released, kept) = light(&stores, "c-hidden");
    assert!(
        released.contains("\n[2] USER: open [link c-hidden:1] [link c-hidden:2]\n"),
        "{released}"
    );
    assert!(
        kept.contains("\n[2] USER: open [link c-hidden:2] [link c-hidden:1]\n"),
        "{kept}"
    );
    assert_eq!(
        released
            .replace(
                "[link c-hidden:1] [link c-hidden:2]",
                "[link c-hidden:2] [link c-hidden:1]"
            )
            .replace("b [link c-hidden:2] end", "b [link c-hidden:1] end"),
        kept
    );
    let (released, kept) = light(&stores, "c-structured");
    let (a, b) = ("A".repeat(40), "B".repeat(40));
    assert!(
        released.contains(&format!("{} … {a}\n", "A".repeat(100))),
        "{released}"
    );
    assert!(
        kept.contains(&format!("{} … {b}\n", "A".repeat(100))),
        "{kept}"
    );
    let (released, kept) = light(&stores, "c-oversized");
    assert!(released.contains("\n[2] USER: big q q") && released.chars().count() > 120_000);
    let marker =
        "120,004 bytes omitted; recall_turn(2, conversation=\"c-oversized\", seq=0) reads it] … ";
    assert!(
        kept.contains("\n[1] USER: first\n[2] USER: big q q") && kept.contains(marker),
        "{kept}"
    );
    assert!(kept.chars().count() < 13_000, "{}", kept.chars().count());
}

#[test]
fn a_turn_records_cannot_carry_is_read_around_and_shown_as_one_line() {
    let stores = stores(
        "handoff-whole",
        &[uncarried("c-whole", false), uncarried("c-whole-last", true)],
    );
    let keeper = stores.kept.keeper().unwrap().unwrap();
    let counted = Counted::new(&keeper);
    let state = stores.kept.state();
    let fixed = |id: &str| {
        format!(
            "[2] [this turn cannot be shown from records; recall_turn(2, conversation=\"{id}\") shows it whole]"
        )
    };
    for view in [View::Light, View::Prompts, View::Full] {
        let kept = shown(state, Some(&counted), "c-whole", view);
        assert!(
            kept.contains(&format!("\n{}\n", fixed("c-whole"))),
            "{kept}"
        );
        assert!(kept.contains("[1] USER: first") && kept.contains("[3] USER: third"));
        assert!(!kept.contains("look") && !kept.contains("cannot be shown in views"));
    }
    // The range read is refused at turn 2 and asked again around it; no
    // read reaches past turn 3 and none reads the conversation whole.
    let asked = counted.asked.borrow();
    let ranges: Vec<_> = asked[..3]
        .iter()
        .map(|r| (r.turn, r.after, r.through))
        .collect();
    assert_eq!(
        ranges,
        [(None, 0, Some(3)), (None, 0, Some(1)), (None, 2, Some(3))]
    );
    drop(asked);
    // Newest, its header is the turn's own: the frame names it as released.
    let (released, kept) = light(&stores, "c-whole-last");
    assert!(kept.ends_with(&format!(
        "{}\n{}",
        fixed("c-whole-last"),
        released.lines().last().unwrap()
    )));
    assert_eq!(kept.lines().next(), released.lines().next());
    // A store without records keeps the released path and bytes.
    let released = shown(&stores.plain, None, "c-whole", View::Light);
    assert!(released.contains("[2] USER: look"));
    let line = "[this conversation cannot be shown in views; it is shown as continue_session has always shown it]";
    let full = shown(&stores.plain, None, "c-whole", View::Full);
    assert_eq!(full, format!("{line}\n{released}"));
}
