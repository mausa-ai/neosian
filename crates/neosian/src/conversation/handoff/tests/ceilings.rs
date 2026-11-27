//! The records handoff's own ceiling and a message of extra data alone
//! (the T1fe rulings J5 and J7): a newest turn of many long prompts is
//! drawn only within its block's ceiling before a line that points to it,
//! while the released handoff still shows it whole; a user message holding
//! only extra data keeps its empty line, as released, its data unshown.

use serde_json::json;

use super::corpus::turn;
use super::{light, stores};

#[test]
fn a_newest_turn_of_many_long_prompts_stays_within_its_ceiling() {
    let id = "c-prompts";
    let prompt =
        |n: usize| json!({"role": "user", "content": format!("P{n:02} {}", "p".repeat(60 << 10))});
    let many: Vec<_> = (0..20).map(prompt).collect();
    let turns = vec![
        turn(id, 1, &[json!({"role": "user", "content": "first"})]),
        turn(id, 2, &many),
    ];
    let held = stores("handoff-ceiling", &[(id, turns, Vec::new())]);
    let (released, kept) = light(&held, id);
    assert!(released.contains("[2] USER: P19 ") && released.chars().count() > 20 * (60 << 10));
    assert!(
        kept.contains("[2] USER: P00 ") && !kept.contains("P19"),
        "{}",
        kept.len()
    );
    let more = "[turn 2 goes on past this view's budget; recall_turn(2, conversation=\"c-prompts\") shows it]";
    assert!(kept.contains(&format!("\n{more}\n")));
    let block = kept.split("\n[2] USER: ").nth(1).unwrap();
    assert!(
        block.chars().count() < 2 * 32_768 + 8_192,
        "{}",
        block.chars().count()
    );
    // A newest turn the released view shows whole stays whole.
    let one = vec![turn(
        "c-one",
        1,
        &[prompt(0), json!({"role": "assistant", "content": "done"})],
    )];
    let single = stores("handoff-ceiling-one", &[("c-one", one, Vec::new())]);
    let (released, kept) = light(&single, "c-one");
    assert_eq!(kept, released);
}

#[test]
fn a_user_message_of_extra_data_alone_keeps_its_empty_line() {
    let id = "c-extra";
    let said = [
        json!({"role": "user", "content": null, "extra": {"meta": "EXTRA-CANARY"}}),
        json!({"role": "assistant", "content": "answer"}),
    ];
    let turns = vec![turn(id, 1, &said), turn(id, 2, &said)];
    let stores = stores("handoff-extra", &[(id, turns, Vec::new())]);
    let (released, kept) = light(&stores, id);
    assert_eq!(kept, released);
    assert!(kept.contains("\n[2] USER: \nAGENT: answer\n"), "{kept}");
    assert!(!kept.contains("EXTRA-CANARY"));
}
