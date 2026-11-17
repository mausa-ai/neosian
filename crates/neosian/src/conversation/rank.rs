//! Ranked and time-bounded history search: history ranking version 1
//! (DESIGN §40.43). Newest keeps the version 1 rule and total order inside
//! the bounds. Relevance scores the candidate turns, those holding at least
//! one term, with the S10 arithmetic over the candidate set itself. Counts
//! fold with Unicode lowercase on every backend; the text is the one the
//! backend matched (files: the decoded turn's, Postgres: the stored one).

use serde::{Deserialize, Serialize};

use super::Turn;
use crate::memory::search::rank::{Best, Facts, Stats};
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

/// One candidate turn as ranking keeps it (DESIGN §40.45): its time, its
/// conversation (an index into the search's own table), its number and, on
/// files, where its log line starts and which line it is.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Key {
    pub created: i64,
    pub conversation: u32,
    pub turn: u64,
    pub place: u64,
    pub line: u32,
}

/// A winner: the key, the conversation id and, under relevance, the score.
pub type Winner = (Key, String, Option<u64>);

/// The two-pass pipeline over candidate turns: pass one offers each turn's
/// searchable text and keeps facts, never content; `winners` scores (relevance)
/// or orders (newest) and names the turns the backend reads back whole.
///
/// Unless `selected`, the bounds and the order's term rule decide membership
/// here (files). `selected` candidates were chosen by the backend within the
/// selection and bounds (Postgres `ILIKE`) and all stay, even one whose counts
/// are all zero because the database folds case differently.
pub struct Ranking {
    order: Order,
    limit: usize,
    bounds: Bounds,
    terms: Vec<String>,
    selected: bool,
    names: Names,
    stats: Stats,
    facts: Facts<Key>,
    newest: Best<Key>,
    /// Newest only: keep turns strictly after this one (size paging, §40.46).
    after: Option<super::page::Last>,
}

impl Ranking {
    pub fn new(request: &Request, selected: bool) -> Result<Self, super::Error> {
        let bounds = request.bounds().map_err(super::Error::Value)?;
        let terms = request.distinct();
        // Only relevance keeps a fact per candidate, so only it reads the bound.
        let maximum = match request.order {
            Order::Relevance => crate::limits::search_candidates()?,
            Order::Newest => 0,
        };
        Ok(Self {
            order: request.order,
            limit: request.limit,
            bounds,
            selected,
            names: Names::Interned(Vec::new(), std::collections::HashMap::new()),
            stats: Stats::new(terms.len()),
            facts: Facts::new(terms.len(), maximum),
            newest: Best::new(request.limit),
            terms,
            after: None,
        })
    }

    /// Newest resumes strictly after `last` in the version 1 order.
    pub fn resume_after(&mut self, last: super::page::Last) {
        self.after = Some(last);
    }

    /// The candidate statistics' digest, which a relevance page binds.
    pub fn digest(&self) -> String {
        self.stats.digest()
    }

    /// The search's own index for a conversation id, interned once.
    pub fn conversation(&mut self, id: &str) -> u32 {
        let Names::Interned(names, index) = &mut self.names else {
            unreachable!("a sorted listing names conversations by position");
        };
        if let Some(index) = index.get(id) {
            return *index;
        }
        let at = u32::try_from(names.len()).expect("conversation count fits u32");
        let shared: std::rc::Rc<str> = std::rc::Rc::from(id);
        names.push(shared.clone());
        index.insert(shared, at);
        at
    }

    /// Adopt a listing of every conversation, sorted bytewise: a
    /// conversation's index is then its position and orders as its id, so
    /// no second copy of the ids is kept (DESIGN §40.46, #417).
    pub fn adopt_sorted(&mut self, ids: Vec<String>) {
        debug_assert!(ids.windows(2).all(|pair| pair[0] < pair[1]));
        self.names = Names::Sorted(ids);
    }

    /// The conversation id at an index.
    pub fn name(&self, index: u32) -> &str {
        self.names.get(index)
    }

    /// Offer one turn: its key and the searchable text its counts come from.
    pub fn offer(&mut self, key: Key, text: &str) -> Result<(), super::Error> {
        if !self.selected && !within(self.bounds, key.created) {
            return Ok(());
        }
        let folded = text.to_lowercase();
        let counts: Vec<u32> = self
            .terms
            .iter()
            .map(|term| u32::try_from(folded.matches(term.as_str()).count()).unwrap_or(u32::MAX))
            .collect();
        let keep = self.selected
            || match self.order {
                Order::Newest => counts.iter().all(|c| *c > 0),
                Order::Relevance => counts.iter().any(|c| *c > 0),
            };
        if !keep {
            return Ok(());
        }
        match self.order {
            Order::Newest => {
                let names = &self.names;
                let id = names.get(key.conversation);
                if self
                    .after
                    .as_ref()
                    .is_some_and(|last| !last.precedes(key.created, id, key.turn))
                {
                    return Ok(());
                }
                self.newest.offer(key, |a, b| newest(names, a, b));
            }
            Order::Relevance => {
                let length = folded.chars().count() as u64;
                self.stats.add(&counts, length);
                self.facts.keep(key, &counts, length).map_err(|broad| {
                    super::Error::Value(crate::limits::broad(
                        broad.0,
                        "turns",
                        "the words or the time range",
                    ))
                })?;
            }
        }
        Ok(())
    }

    /// The winners in answer order: relevance by score, then the version 1
    /// total order; newest by that order alone.
    pub fn winners(self) -> Vec<Winner> {
        let names = self.names;
        let ranked: Vec<(Key, Option<u64>)> = match self.order {
            Order::Newest => self
                .newest
                .into_vec(|a, b| newest(&names, a, b))
                .into_iter()
                .map(|k| (k, None))
                .collect(),
            Order::Relevance => self
                .facts
                .best(&self.stats, self.limit, |a, b| newest(&names, a, b))
                .into_iter()
                .map(|(key, score)| (key, Some(score)))
                .collect(),
        };
        ranked
            .into_iter()
            .map(|(key, score)| (key, names.get(key.conversation).to_string(), score))
            .collect()
    }
}

/// The conversation ids a search refers to by index.
enum Names {
    /// Interned as met (Postgres, and files given a list): one shared copy
    /// of each id, in the table and in the lookup.
    Interned(
        Vec<std::rc::Rc<str>>,
        std::collections::HashMap<std::rc::Rc<str>, u32>,
    ),
    /// A listing sorted bytewise (files over every conversation).
    Sorted(Vec<String>),
}

impl Names {
    fn get(&self, index: u32) -> &str {
        match self {
            Names::Interned(names, _) => &names[index as usize],
            Names::Sorted(names) => &names[index as usize],
        }
    }

    /// Two indexes in their ids' bytewise order.
    fn order(&self, a: u32, b: u32) -> std::cmp::Ordering {
        match self {
            Names::Interned(names, _) => names[a as usize].cmp(&names[b as usize]),
            Names::Sorted(_) => a.cmp(&b),
        }
    }
}

/// The version 1 total order: `created_at`, conversation id, turn, each descending.
fn newest(names: &Names, a: &Key, b: &Key) -> std::cmp::Ordering {
    b.created
        .cmp(&a.created)
        .then_with(|| names.order(b.conversation, a.conversation))
        .then_with(|| b.turn.cmp(&a.turn))
}

#[cfg(test)]
mod tests {
    use super::{Key, Order, Ranking, Request};

    fn request(order: Order, limit: usize) -> Request {
        Request {
            terms: vec!["x".into()],
            ids: None,
            limit,
            order,
            since: None,
            before: None,
        }
    }

    fn key(conversation: u32, turn: u64, created: i64) -> Key {
        Key {
            created,
            conversation,
            turn,
            place: 0,
            line: 0,
        }
    }

    #[test]
    fn newest_keeps_limit_keys_in_the_version_1_order() {
        let mut ranking = Ranking::new(&request(Order::Newest, 2), false).unwrap();
        let (a, b) = (ranking.conversation("a"), ranking.conversation("b"));
        assert_eq!(ranking.conversation("a"), a);
        for (conversation, turn, created, text) in [
            (a, 1, 10, "x"),
            (b, 1, 10, "X"),
            (a, 2, 30, "y"),
            (a, 3, 20, "x x"),
        ] {
            ranking
                .offer(key(conversation, turn, created), text)
                .unwrap();
        }
        let winners: Vec<(String, u64, Option<u64>)> = ranking
            .winners()
            .into_iter()
            .map(|(key, id, score)| (id, key.turn, score))
            .collect();
        // Time descending, then conversation id descending; y holds no term.
        assert_eq!(winners, [("a".into(), 3, None), ("b".into(), 1, None)]);
    }

    #[test]
    fn selected_candidates_stay_even_with_zero_counts() {
        let mut ranking = Ranking::new(&request(Order::Relevance, 5), true).unwrap();
        let a = ranking.conversation("a");
        ranking.offer(key(a, 1, 10), "x").unwrap();
        ranking.offer(key(a, 2, 20), "folded elsewhere").unwrap();
        let winners = ranking.winners();
        assert_eq!(winners.len(), 2);
        assert_eq!(winners[0].0.turn, 1);
        assert_eq!(winners[1].2, Some(0));
    }
}
