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
    let mut lines: Vec<(u64, u64, String)> = Vec::new();
    let mut emitted = HashSet::new();
    for turn in turns {
        match winner.get(&turn.turn) {
            None => {
                let text = log_line(turn, digest_chars, user_chars, links);
                lines.push((turn.turn, turn.turn, format!("[{}] {text}", turn.turn)));
            }
            Some(&index) => {
                if emitted.insert(index) {
                    let entry = &entries[index];
                    let first = entry.turn - entry.span + 1;
                    lines.push((first, entry.turn, entry_line(entry)));
                }
            }
        }
    }
    let (mut kept, mut used) = (0, 0);
    for (_, _, line) in lines.iter().rev() {
        if used + len(line) + 1 > budget_chars {
            break;
        }
        used += len(line) + 1;
        kept += 1;
    }
    if kept == lines.len() {
        return lines.into_iter().map(|(_, _, line)| line).collect();
    }
    let template = folded(CONTEXT, "view_fold").expect("context.yaml carries the fold line");
    let fold = loop {
        let folded = &lines[..lines.len() - kept];
        let (first, last) = (folded[0].0, folded[folded.len() - 1].1);
        let fold = render(
            &template,
            &[
                ("first", &first.to_string()),
                ("last", &last.to_string()),
                ("count", &(last - first + 1).to_string()),
            ],
        );
        if kept == 0 || used + len(&fold) < budget_chars {
            break fold;
        }
        used -= len(&lines[lines.len() - kept].2) + 1;
        kept -= 1;
    };
    let kept = lines.drain(lines.len() - kept..).map(|(_, _, line)| line);
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
