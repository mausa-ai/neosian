//! A reduced turn drawn in a view: the verbatim block a newest turn gets
//! and the log line an older one gets. The default view draws exactly what
//! the released handoff draws from a whole turn. `prompts` keeps the user
//! prompts alone; `calls` draws each call with its arguments wider and
//! its result by its size alone; `full` adds each result's wider head and
//! tail and its size to the block. An older turn's line in `calls` keeps
//! results by their size, as its block does. A turn records cannot carry
//! is its one fixed line in every view.

use super::build::{Outcome, Preview, Round, Said};
use crate::assets::{CONTEXT, quoted, render};
use crate::conversation::projection::{REDACTED, SEPARATOR, round_line};
use crate::conversation::recall::{View, grouped};
use crate::text::{head, len, rstrip};

impl Preview {
    /// The turn as a verbatim block shows it.
    pub fn block(&self, view: View) -> String {
        if let Some(fixed) = self.fixed() {
            return fixed;
        }
        let lines: Vec<String> = self
            .said
            .iter()
            .filter_map(|s| self.drawn(s, view))
            .collect();
        lines.join("\n")
    }

    /// The block, its lines kept while it stays within `ceiling`
    /// characters, the first cut short when it alone passes; and whether
    /// anything was left out. No more of the block is drawn than it keeps.
    pub fn block_within(&self, view: View, ceiling: usize) -> (String, bool) {
        if let Some(fixed) = self.fixed() {
            return (fixed, false);
        }
        let (mut block, mut used, mut started) = (String::new(), 0, false);
        for drawn in self.said.iter().filter_map(|said| self.drawn(said, view)) {
            for line in drawn.split('\n') {
                let cost = len(line) + usize::from(started);
                if used + cost > ceiling {
                    if !started {
                        block.push_str(head(line, ceiling));
                    }
                    return (block, true);
                }
                if started {
                    block.push('\n');
                }
                block.push_str(line);
                (used, started) = (used + cost, true);
            }
        }
        (block, false)
    }

    /// The one line a turn records cannot carry or a redacted turn is.
    fn fixed(&self) -> Option<String> {
        if let Some(fixed) = &self.fixed {
            return Some(fixed.clone());
        }
        self.redacted.then(|| format!("[{}] {REDACTED}", self.turn))
    }

    /// What one entry draws in a block.
    fn drawn(&self, said: &Said, view: View) -> Option<String> {
        match said {
            Said::User { text, .. } => Some(format!("[{}] USER: {text}", self.turn)),
            _ if view == View::Prompts => None,
            Said::Agent {
                text: Some(text), ..
            } => Some(format!("AGENT: {text}")),
            Said::Agent { line, .. } => Some(line.clone()),
            Said::Call(round) => Some(drawn(round, view, true)),
        }
    }

    /// The turn as one log line shows it, labeled `[n]`.
    pub fn line(&self, view: View) -> String {
        if let Some(fixed) = self.fixed() {
            return fixed;
        }
        let segments: Vec<String> = self
            .said
            .iter()
            .filter_map(|said| match said {
                Said::User { line, .. } => Some(line.clone()),
                _ if view == View::Prompts => None,
                Said::Agent { line, .. } => Some(line.clone()),
                Said::Call(round) => Some(drawn(round, view, false)),
            })
            .collect();
        let line = format!("[{}] {}", self.turn, segments.join(SEPARATOR));
        match view {
            View::Light => line,
            _ => rstrip(&line).to_string(),
        }
    }
}

/// One call as `view` draws it in a block or a line.
fn drawn(round: &Round, view: View, block: bool) -> String {
    let sized = |outcome: &Outcome| {
        let template = quoted(CONTEXT, "continue_size").expect("context.yaml has the size");
        render(&template, &[("bytes", &grouped(outcome.size))])
    };
    let outcome = round.outcome.as_ref();
    match (view, block) {
        (View::Calls, _) => {
            let args = if block { &round.wide } else { &round.args };
            round_line(&round.name, args, outcome.map(sized).as_deref())
        }
        (View::Full, true) => {
            let shown = outcome.map(|outcome| match outcome.wide.as_str() {
                "" => sized(outcome),
                wide => format!("{wide} {}", sized(outcome)),
            });
            round_line(&round.name, &round.wide, shown.as_deref())
        }
        _ => {
            let digest = outcome.map(|outcome| outcome.digest.as_str());
            round_line(&round.name, &round.args, digest)
        }
    }
}
