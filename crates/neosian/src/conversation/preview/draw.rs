//! A reduced turn drawn in a view: the verbatim block a newest turn gets
//! and the log line an older one gets. The default view draws exactly what
//! the released handoff draws from a whole turn. `prompts` keeps the user
//! prompts alone; `calls` draws each call with its arguments wider and
//! its result by its size alone; `full` adds each result's wider head and
//! tail and its size to the block. An older turn's line in `calls` keeps
//! results by their size, as its block does.

use super::build::{Outcome, Preview, Round, Said};
use crate::assets::{CONTEXT, quoted, render};
use crate::conversation::projection::{REDACTED, SEPARATOR, round_line};
use crate::conversation::recall::{View, grouped};
use crate::text::rstrip;

impl Preview {
    /// The turn as a verbatim block shows it.
    pub fn block(&self, view: View) -> String {
        if self.redacted {
            return format!("[{}] {REDACTED}", self.turn);
        }
        let mut lines = Vec::new();
        for said in &self.said {
            match said {
                Said::User { text, .. } => lines.push(format!("[{}] USER: {text}", self.turn)),
                _ if view == View::Prompts => {}
                Said::Agent {
                    text: Some(text), ..
                } => lines.push(format!("AGENT: {text}")),
                Said::Agent { line, .. } => lines.push(line.clone()),
                Said::Call(round) => lines.push(drawn(round, view, true)),
            }
        }
        lines.join("\n")
    }

    /// The turn as one log line shows it, labeled `[n]`.
    pub fn line(&self, view: View) -> String {
        if self.redacted {
            return format!("[{}] {REDACTED}", self.turn);
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
