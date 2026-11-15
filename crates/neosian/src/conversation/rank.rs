//! Ranked and time-bounded history search: history ranking version 1
//! (DESIGN §40.43). Newest keeps the version 1 rule and total order inside
//! the bounds. Relevance scores the candidate turns, those holding at least
//! one term, with the S10 arithmetic over the candidate set itself. Counts
//! fold with Unicode lowercase on every backend; the text is the one the
//! backend matched (files: the decoded turn's, Postgres: the stored one).

use serde::{Deserialize, Serialize};

use super::Turn;
use crate::memory::search::rank::Stats;
use crate::store::time::Stamp;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize, clap::ValueEnum)]
#[serde(rename_all = "lowercase")]
pub enum Order {
    Newest,
    Relevance,
}

impl Order {
    pub fn name(self) -> &'static str {
        match self {
            Order::Newest => "newest",
            Order::Relevance => "relevance",
        }
    }
}

/// A turn and, under relevance, its quantized score.
pub type Ranked = (Turn, Option<u64>);

/// Since and before as UTC microseconds: since inclusive, before exclusive.
pub type Bounds = (Option<i64>, Option<i64>);

/// Whether an instant lies inside the bounds; turns and the agent doors'
/// message hits share the rule (§40.43).
pub fn within((since, before): Bounds, at: i64) -> bool {
    since.is_none_or(|s| at >= s) && before.is_none_or(|b| at < b)
}

#[derive(Clone, Debug)]
pub struct Request {
    /// Lowercased terms as the version 1 query parser gives them.
    pub terms: Vec<String>,
    pub ids: Option<Vec<String>>,
    pub limit: usize,
    pub order: Order,
    pub since: Option<String>,
    pub before: Option<String>,
}

impl Request {
    /// The bounds as UTC microseconds; since must precede before.
    pub fn bounds(&self) -> Result<Bounds, String> {
        let instant = |value: &Option<String>, name: &str| {
            value
                .as_deref()
                .map(|text| {
                    Stamp::parse(text)
                        .ok()
                        .filter(|_| text.ends_with('Z'))
                        .map(Stamp::instant)
                        .ok_or_else(|| format!("{name} must be a UTC RFC 3339 instant ending in Z"))
                })
                .transpose()
        };
        let bounds = (
            instant(&self.since, "since")?,
            instant(&self.before, "before")?,
        );
        if let (Some(since), Some(before)) = bounds
            && since >= before
        {
            return Err("since must precede before".into());
        }
        Ok(bounds)
    }

    /// Distinct terms in ascending codepoint order, the scoring order.
    fn distinct(&self) -> Vec<String> {
        let mut terms = self.terms.clone();
        terms.sort();
        terms.dedup();
        terms
    }
}

/// The version 1 total order: `created_at`, conversation id, turn, each descending.
fn newest(a: &Turn, b: &Turn) -> std::cmp::Ordering {
    (b.created_at.instant(), &b.conversation_id, b.turn).cmp(&(
        a.created_at.instant(),
        &a.conversation_id,
        a.turn,
    ))
}

/// Score and order candidate turns, each with the searchable text its counts
/// come from. Unless `selected`, the bounds and the order's term rule decide
/// membership here (files). `selected` candidates were chosen by the backend
/// within the selection and bounds (Postgres `ILIKE`) and all stay, even one
/// whose counts are all zero because the database folds case differently.
pub fn rank(
    candidates: Vec<(Turn, String)>,
    request: &Request,
    selected: bool,
) -> Result<Vec<Ranked>, String> {
    let bounds = request.bounds()?;
    let terms = request.distinct();
    let mut kept = Vec::new();
    for (turn, text) in candidates {
        if !selected && !within(bounds, turn.created_at.instant()) {
            continue;
        }
        let folded = text.to_lowercase();
        let counts: Vec<u32> = terms
            .iter()
            .map(|term| u32::try_from(folded.matches(term.as_str()).count()).unwrap_or(u32::MAX))
            .collect();
        let keep = selected
            || match request.order {
                Order::Newest => counts.iter().all(|c| *c > 0),
                Order::Relevance => counts.iter().any(|c| *c > 0),
            };
        if keep {
            kept.push((turn, counts, folded.chars().count() as u64));
        }
    }
    if request.order == Order::Newest {
        let mut turns: Vec<Turn> = kept.into_iter().map(|(turn, _, _)| turn).collect();
        turns.sort_by(newest);
        turns.truncate(request.limit);
        return Ok(turns.into_iter().map(|turn| (turn, None)).collect());
    }
    let mut stats = Stats::new(terms.len());
    for (_, counts, length) in &kept {
        stats.add(counts, *length);
    }
    let mut scored: Vec<(u64, Turn)> = kept
        .into_iter()
        .map(|(turn, counts, length)| (stats.score(&counts, length), turn))
        .collect();
    scored.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| newest(&a.1, &b.1)));
    scored.truncate(request.limit);
    Ok(scored
        .into_iter()
        .map(|(score, turn)| (turn, Some(score)))
        .collect())
}
