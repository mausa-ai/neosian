//! A conversation as one log line per turn (`conversation/views.py`,
//! DESIGN §21): the winning entry where one covers a turn, the
//! deterministic line elsewhere, the oldest folded into one count line
//! under a budget so the block always says how much it hides.

use std::collections::HashSet;

use super::links::LinkRegistry;
use super::projection::{entry_line, log_line, select};
use super::{Projection, Turn};
use crate::assets::{CONTEXT, folded, render};
use crate::text::len;

/// Every turn as one log line, the newest kept within `budget_chars`;
/// the fold line counts too and displaces the oldest kept line until
/// the whole fits.
pub fn project_conversation(
    turns: &[Turn],
    entries: &[Projection],
    digest_chars: usize,
    user_chars: usize,
    budget_chars: usize,
    links: &LinkRegistry,
) -> Vec<String> {
    let winner = select(entries);
    let mut lines: Vec<Line> = Vec::new();
    let mut emitted = HashSet::new();
    for turn in turns {
        match winner.get(&turn.turn) {
            None => {
                let text = log_line(turn, digest_chars, user_chars, links);
                let text = format!("[{}] {text}", turn.turn);
                lines.push(Line::new(turn.turn, turn.turn, text));
            }
            Some(&index) => {
                if emitted.insert(index) {
                    let entry = &entries[index];
                    let first = entry.turn - entry.span + 1;
                    lines.push(Line::new(first, entry.turn, entry_line(entry)));
                }
            }
        }
    }
    let start = lines.first().map_or(0, |line| line.first);
    fit(lines, start, budget_chars)
}

/// One log line: the turns it covers, its text and its length in
/// characters. A line no budget can keep may carry its length alone.
pub struct Line {
    pub first: u64,
    pub last: u64,
    pub text: String,
    pub len: usize,
}

impl Line {
    pub fn new(first: u64, last: u64, text: String) -> Self {
        let len = len(&text);
        Self {
            first,
            last,
            text,
            len,
        }
    }
}

/// The newest lines kept within `budget_chars`, the fold line counting
/// too; `start` is the first turn of the oldest line, which a streamed
/// list may already have let go because no budget could keep it.
pub fn fit(mut lines: Vec<Line>, start: u64, budget_chars: usize) -> Vec<String> {
    let (mut kept, mut used) = (0, 0);
    for line in lines.iter().rev() {
        if used + line.len + 1 > budget_chars {
            break;
        }
        used += line.len + 1;
        kept += 1;
    }
    if kept == lines.len() {
        return lines.into_iter().map(|line| line.text).collect();
    }
    let template = folded(CONTEXT, "view_fold").expect("context.yaml carries the fold line");
    let fold = loop {
        let last = lines[lines.len() - kept - 1].last;
        let fold = render(
            &template,
            &[
                ("first", &start.to_string()),
                ("last", &last.to_string()),
                ("count", &(last - start + 1).to_string()),
            ],
        );
        if kept == 0 || used + len(&fold) < budget_chars {
            break fold;
        }
        used -= lines[lines.len() - kept].len + 1;
        kept -= 1;
    };
    let kept = lines.drain(lines.len() - kept..).map(|line| line.text);
    std::iter::once(fold).chain(kept).collect()
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::super::message::from_json;
    use super::*;
    use crate::store::time::Stamp;

    fn turns(count: u64) -> Vec<Turn> {
        (1..=count)
            .map(|number| Turn {
                conversation_id: "c".to_string(),
                turn: number,
                messages: vec![
                    from_json(&json!({
                        "role": "user",
                        "content": format!("prompt {number} {}", "p".repeat(51)),
                    }))
                    .unwrap(),
                ],
                created_at: Stamp::now(),
                actor: None,
                redacted: false,
            })
            .collect()
    }

    #[test]
    fn every_turn_is_one_line_and_the_oldest_fold() {
        let turns = turns(4);
        let entries = vec![Projection {
            turn: 2,
            kind: "epoch".into(),
            text: "folded".into(),
            span: 2,
        }];
        let links = LinkRegistry::of(&turns, None);
        let line = |number: u64| format!("[{number}] USER: prompt {number} {}", "p".repeat(51));
        let whole = project_conversation(&turns, &entries, 200, 800, 1000, &links);
        assert_eq!(whole, ["[1-2] folded".to_string(), line(3), line(4)]);
        // Two lines fit in 150 and the fold line does not beside them, so
        // it displaces the older one and names three turns.
        let tight = project_conversation(&turns, &entries, 200, 800, 150, &links);
        let fold = "[1-3] 3 earlier turns not shown".to_string();
        assert_eq!(tight, [fold, line(4)]);
        let none = project_conversation(&turns, &entries, 200, 800, 1, &links);
        assert_eq!(none, ["[1-4] 4 earlier turns not shown"]);
    }
}
