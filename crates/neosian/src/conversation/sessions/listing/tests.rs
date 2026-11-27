//! The listing's accounting: first and later landings, a duplicate
//! receipt, a gap, a malformed or stale prior pair, a turn sent whole,
//! and what an erasure clears.

use super::*;

const PROMPT_REPLY: Landed = Landed {
    kinds: Kinds(0b11),
    bytes: 12,
};
const TOOLS: Landed = Landed {
    kinds: Kinds(0b1101),
    bytes: 812_388,
};

/// A sessions document through turn `turns` listing `pair`.
fn document(turns: u64, pair: &str) -> String {
    format!(
        "# codex session s\n\n- agent: codex\n- conversation: s\n\
         - started: 2026-11-27T10:00:00.000000Z\n- last prompt: go\n\
         - turns: {turns}\n{pair}- continues: a\n- parent: p\n"
    )
}

fn listed(turns: u64, pair: Option<Count>) -> String {
    document(turns, &pair.map_or(String::new(), |count| count.lines()))
}

#[test]
fn the_first_landing_starts_the_count_and_later_landings_add_to_it() {
    let first = next(None, 1, Some(&PROMPT_REPLY)).unwrap();
    assert_eq!(
        first.lines(),
        "- recorded: prompt, reply\n- size: 12 bytes\n"
    );
    let second = next(Some(&listed(1, Some(first))), 2, Some(&TOOLS)).unwrap();
    assert_eq!(
        second.lines(),
        "- recorded: prompt, reply, tool_call, tool_result\n- size: 812400 bytes\n"
    );
    let reasoning = Landed {
        kinds: Kinds(0b1010_0001),
        bytes: 1,
    };
    let third = next(Some(&listed(2, Some(second))), 3, Some(&reasoning)).unwrap();
    assert_eq!(
        third.lines(),
        "- recorded: prompt, reply, tool_call, tool_result, reasoning, extra\n\
         - size: 812401 bytes\n"
    );
}

#[test]
fn a_duplicate_receipt_adds_nothing() {
    let first = next(None, 1, Some(&PROMPT_REPLY)).unwrap();
    let second = next(Some(&listed(1, Some(first))), 2, Some(&TOOLS)).unwrap();
    let again = next(Some(&listed(2, Some(second))), 2, Some(&TOOLS)).unwrap();
    assert_eq!(again, second);
    assert_eq!(next(Some(&listed(2, None)), 2, Some(&TOOLS)), None);
}

#[test]
fn a_gap_omits_the_pair_and_the_next_landing_starts_again() {
    let first = next(None, 1, Some(&PROMPT_REPLY)).unwrap();
    // Turn 2 was stored but its document never written (ruling E1).
    assert_eq!(next(Some(&listed(1, Some(first))), 3, Some(&TOOLS)), None);
    let again = next(Some(&listed(3, None)), 4, Some(&TOOLS)).unwrap();
    assert_eq!(
        again.lines(),
        "- recorded: prompt, tool_call, tool_result\n- size: 812388 bytes since turn 4\n"
    );
    let on = next(Some(&listed(4, Some(again))), 5, Some(&PROMPT_REPLY)).unwrap();
    assert_eq!(
        on.lines(),
        "- recorded: prompt, reply, tool_call, tool_result\n\
         - size: 812400 bytes since turn 4\n"
    );
    // A document this landing creates past turn 1 says where it starts.
    let created = next(None, 3, Some(&PROMPT_REPLY)).unwrap();
    assert!(created.lines().ends_with("- size: 12 bytes since turn 3\n"));
}

#[test]
fn a_whole_turn_or_a_stale_landing_lists_no_pair() {
    let first = next(None, 1, Some(&PROMPT_REPLY)).unwrap();
    assert_eq!(next(Some(&listed(1, Some(first))), 2, None), None);
    assert_eq!(next(None, 1, None), None);
    assert_eq!(next(Some(&listed(5, Some(first))), 4, Some(&TOOLS)), None);
    // The landing after the whole turn starts again.
    let after = next(Some(&listed(2, None)), 3, Some(&TOOLS)).unwrap();
    assert!(after.lines().ends_with("since turn 3\n"));
}

#[test]
fn a_malformed_prior_pair_omits_the_pair() {
    let malformed = [
        "- recorded: prompt\n",
        "- size: 12 bytes\n",
        "- recorded: reply, prompt\n- size: 12 bytes\n",
        "- recorded: prompt, prompt\n- size: 12 bytes\n",
        "- recorded: prompt, thought\n- size: 12 bytes\n",
        "- recorded: \n- size: 12 bytes\n",
        "- recorded: prompt\n- size: 012 bytes\n",
        "- recorded: prompt\n- size: 12 bytes since turn 1\n",
        "- recorded: prompt\n- size: 12 bytes since turn 0\n",
        "- recorded: prompt\n- size: 12 bytes since turn 9\n",
        "- recorded: prompt\n- size: 12 KiB\n",
        "- recorded: prompt\n- size: 12 bytes\n- size: 12 bytes\n",
    ];
    for pair in malformed {
        assert_eq!(
            next(Some(&document(2, pair)), 3, Some(&TOOLS)),
            None,
            "{pair}"
        );
    }
    let unnumbered = document(2, "").replace("- turns: 2\n", "- turns: two\n");
    assert_eq!(next(Some(&unnumbered), 3, Some(&TOOLS)), None);
    let well = "- recorded: prompt\n- size: 12 bytes since turn 2\n";
    let count = next(Some(&document(2, well)), 3, Some(&TOOLS)).unwrap();
    assert!(
        count
            .lines()
            .ends_with("- size: 812400 bytes since turn 2\n")
    );
    let overflow = format!("- recorded: prompt\n- size: {} bytes\n", u64::MAX);
    assert_eq!(next(Some(&document(2, &overflow)), 3, Some(&TOOLS)), None);
}

#[test]
fn an_erasure_that_covers_the_counted_turns_clears_the_pair() {
    let pair = "- recorded: prompt\n- size: 12 bytes since turn 4\n";
    let content = document(6, pair);
    let plain = document(6, "");
    assert_eq!(cleared(&content, &Erased::Through(3)), None);
    assert_eq!(cleared(&content, &Erased::Turns(&[1, 3, 7])), None);
    assert_eq!(cleared(&content, &Erased::Through(4)).unwrap(), plain);
    assert_eq!(cleared(&content, &Erased::Turns(&[2, 6])).unwrap(), plain);
    assert_eq!(cleared(&plain, &Erased::Through(9)), None);
    let whole = document(6, "- recorded: prompt\n- size: 12 bytes\n");
    assert_eq!(cleared(&whole, &Erased::Through(1)).unwrap(), plain);
    let broken = document(6, "- size: 12 bytes\n");
    assert_eq!(cleared(&broken, &Erased::Turns(&[9])).unwrap(), plain);
}
