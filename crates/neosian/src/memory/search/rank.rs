//! Pinned relevance arithmetic for memory ranking version 1 (DESIGN §40.41)
//! and the ranking pipeline memory and history search share (§40.45).
//!
//! BM25 over version 1 substring occurrence counts. The statistics come from
//! the authorized, eligible selection of one scope; the score is quantized to
//! integer micro-points so independent implementations agree on order.
//! A ranked search keeps small facts per candidate, never its content, and
//! reads content again for the winners only.

use std::cmp::Ordering;
use std::collections::HashMap;

const K1: f64 = 1.2;
const B: f64 = 0.75;

/// The `limit` first items of a stream under a total order, holding at most
/// `limit` of them. Equal items keep their arrival order, as a stable sort
/// followed by a cut would. A binary heap whose top is the worst item kept,
/// so an offer costs O(log limit) (#417).
pub struct Best<T> {
    items: Vec<(T, u64)>,
    limit: usize,
    arrived: u64,
}

impl<T> Best<T> {
    pub fn new(limit: usize) -> Self {
        Self {
            items: Vec::new(),
            limit,
            arrived: 0,
        }
    }

    /// `order(a, b)` is `Less` when `a` comes first.
    pub fn offer(&mut self, item: T, order: impl Fn(&T, &T) -> Ordering) {
        let entry = (item, self.arrived);
        self.arrived += 1;
        if self.limit == 0 {
            return;
        }
        let later = |a: &(T, u64), b: &(T, u64)| order(&a.0, &b.0).then(a.1.cmp(&b.1));
        if self.items.len() < self.limit {
            self.items.push(entry);
            let mut at = self.items.len() - 1;
            while at > 0 {
                let parent = (at - 1) / 2;
                if later(&self.items[at], &self.items[parent]) != Ordering::Greater {
                    break;
                }
                self.items.swap(at, parent);
                at = parent;
            }
            return;
        }
        if later(&entry, &self.items[0]) != Ordering::Less {
            return;
        }
        self.items[0] = entry;
        let mut at = 0;
        loop {
            let (left, right) = (2 * at + 1, 2 * at + 2);
            let mut worst = at;
            for child in [left, right] {
                if child < self.items.len()
                    && later(&self.items[child], &self.items[worst]) == Ordering::Greater
                {
                    worst = child;
                }
            }
            if worst == at {
                break;
            }
            self.items.swap(at, worst);
            at = worst;
        }
    }

    /// The items kept, first first.
    pub fn into_vec(mut self, order: impl Fn(&T, &T) -> Ordering) -> Vec<T> {
        self.items
            .sort_by(|a, b| order(&a.0, &b.0).then(a.1.cmp(&b.1)));
        self.items.into_iter().map(|(item, _)| item).collect()
    }
}

/// A candidate set larger than the operator's bound (`NEOSIAN_SEARCH_MAX_CANDIDATES`).
#[derive(Debug)]
pub struct Broad(pub usize);

/// What relevance keeps per candidate: a key, its per-term counts and its length.
pub struct Facts<K> {
    keys: Vec<K>,
    counts: Vec<u32>,
    lengths: Vec<u64>,
    terms: usize,
    maximum: usize,
}

impl<K> Facts<K> {
    pub fn new(terms: usize, maximum: usize) -> Self {
        Self {
            keys: Vec::new(),
            counts: Vec::new(),
            lengths: Vec::new(),
            terms,
            maximum,
        }
    }

    pub fn keep(&mut self, key: K, counts: &[u32], length: u64) -> Result<(), Broad> {
        if self.keys.len() >= self.maximum {
            return Err(Broad(self.maximum));
        }
        debug_assert_eq!(counts.len(), self.terms);
        self.keys.push(key);
        self.counts.extend_from_slice(counts);
        self.lengths.push(length);
        Ok(())
    }

    /// The `limit` best candidates with their scores: score descending, then
    /// `order` (the existing tie order).
    pub fn best(
        self,
        stats: &Stats,
        limit: usize,
        order: impl Fn(&K, &K) -> Ordering,
    ) -> Vec<(K, u64)> {
        let terms = self.terms;
        let mut best = Best::new(limit);
        let ranked = |a: &(u64, usize), b: &(u64, usize)| {
            b.0.cmp(&a.0)
                .then_with(|| order(&self.keys[a.1], &self.keys[b.1]))
        };
        for (index, length) in self.lengths.iter().enumerate() {
            let counts = &self.counts[index * terms..(index + 1) * terms];
            best.offer((stats.score(counts, *length), index), ranked);
        }
        let chosen = best.into_vec(ranked);
        let mut wanted: HashMap<usize, Option<K>> =
            chosen.iter().map(|(_, index)| (*index, None)).collect();
        for (index, key) in self.keys.into_iter().enumerate() {
            if let Some(slot) = wanted.get_mut(&index) {
                *slot = Some(key);
            }
        }
        chosen
            .into_iter()
            .map(|(score, index)| {
                let key = wanted.get_mut(&index).and_then(Option::take);
                (key.expect("every winner has its key"), score)
            })
            .collect()
    }
}

/// Corpus statistics: eligible documents, their total length, and per-term
/// document frequency in ascending term order.
pub struct Stats {
    documents: u64,
    length: u64,
    frequency: Vec<u64>,
}

impl Stats {
    pub fn new(terms: usize) -> Self {
        Self {
            documents: 0,
            length: 0,
            frequency: vec![0; terms],
        }
    }

    pub fn add(&mut self, counts: &[u32], length: u64) {
        self.documents += 1;
        self.length += length;
        for (frequency, count) in self.frequency.iter_mut().zip(counts) {
            if *count > 0 {
                *frequency += 1;
            }
        }
    }

    /// A short digest of everything scores depend on beyond one candidate's
    /// own counts: a size-paged relevance answer refuses when it changes
    /// between pages (DESIGN §40.46).
    pub fn digest(&self) -> String {
        use sha2::{Digest, Sha256};
        let text = format!("{}:{}:{:?}", self.documents, self.length, self.frequency);
        Sha256::digest(text.as_bytes())[..8]
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect()
    }

    /// The quantized score: round-half-even of BM25 times one million.
    #[allow(clippy::cast_precision_loss, clippy::cast_possible_truncation)]
    #[allow(clippy::cast_sign_loss)]
    pub fn score(&self, counts: &[u32], length: u64) -> u64 {
        let documents = self.documents as f64;
        let average = self.length as f64 / documents;
        let mut total = 0.0_f64;
        for (count, frequency) in counts.iter().zip(&self.frequency) {
            if *count == 0 {
                continue;
            }
            let (tf, df) = (f64::from(*count), *frequency as f64);
            let idf = (1.0 + (documents - df + 0.5) / (df + 0.5)).ln();
            total += idf * tf * (K1 + 1.0) / (tf + K1 * (1.0 - B + B * length as f64 / average));
        }
        (total * 1e6).round_ties_even() as u64
    }
}

#[cfg(test)]
mod tests {
    use super::{Best, Broad, Facts, Stats};

    #[test]
    fn best_keeps_the_first_items_and_arrival_order_among_equals() {
        let mut best = Best::new(3);
        for item in [(5, 'a'), (9, 'b'), (5, 'c'), (7, 'd'), (5, 'e'), (9, 'f')] {
            best.offer(item, |x, y| y.0.cmp(&x.0));
        }
        assert_eq!(
            best.into_vec(|x, y| y.0.cmp(&x.0)),
            [(9, 'b'), (9, 'f'), (7, 'd')]
        );
        let mut stable = Best::new(4);
        for item in [(1, 'a'), (1, 'b'), (2, 'c'), (1, 'd'), (1, 'e')] {
            stable.offer(item, |x, y| y.0.cmp(&x.0));
        }
        // A stable sort then a cut gives c, a, b, d.
        assert_eq!(
            stable.into_vec(|x, y| y.0.cmp(&x.0)),
            [(2, 'c'), (1, 'a'), (1, 'b'), (1, 'd')]
        );
        let mut none = Best::new(0);
        none.offer(1, |x: &i32, y| x.cmp(y));
        assert!(none.into_vec(|x: &i32, y| x.cmp(y)).is_empty());
        // Against a stable sort and a cut, over many orders of many equals.
        let mut seed = 7_u64;
        for limit in [1, 2, 5, 17, 64] {
            let items: Vec<(u64, usize)> = (0..300)
                .map(|index| {
                    seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
                    ((seed >> 33) % 23, index)
                })
                .collect();
            let mut best = Best::new(limit);
            for item in &items {
                best.offer(*item, |x, y| y.0.cmp(&x.0));
            }
            let mut sorted = items.clone();
            sorted.sort_by_key(|x| std::cmp::Reverse(x.0));
            sorted.truncate(limit);
            assert_eq!(best.into_vec(|x, y| y.0.cmp(&x.0)), sorted);
        }
    }

    #[test]
    fn facts_rank_as_scoring_every_candidate_would_and_refuse_past_the_bound() {
        let candidates: [(&str, &[u32], u64); 4] = [
            ("d", &[3, 1], 57),
            ("c", &[1, 2], 40),
            ("b", &[0, 1], 28),
            ("a", &[1, 0], 51),
        ];
        let mut stats = Stats::new(2);
        let mut facts = Facts::new(2, 4);
        for (key, counts, length) in candidates {
            stats.add(counts, length);
            facts.keep(key, counts, length).unwrap();
        }
        let mut every: Vec<(&str, u64)> = candidates
            .iter()
            .map(|(key, counts, length)| (*key, stats.score(counts, *length)))
            .collect();
        every.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(b.0)));
        every.truncate(3);
        assert_eq!(facts.best(&stats, 3, |a, b| a.cmp(b)), every);
        let mut bounded = Facts::new(1, 2);
        bounded.keep(1, &[1], 1).unwrap();
        bounded.keep(2, &[1], 1).unwrap();
        assert!(matches!(bounded.keep(3, &[1], 1), Err(Broad(2))));
    }

    /// Expected values come from an independent Python reference of the
    /// DESIGN §40.41 formula, never from this implementation.
    #[test]
    fn scores_match_the_independent_reference() {
        // Four documents, terms ["deploy", "review"], path and content selected.
        let documents: [(&[u32], u64); 4] =
            [(&[3, 1], 57), (&[1, 2], 40), (&[0, 0], 28), (&[1, 0], 51)];
        let mut stats = Stats::new(2);
        for (counts, length) in documents {
            stats.add(counts, length);
        }
        assert_eq!(stats.score(&[3, 1], 57), 1_145_519);
        assert_eq!(stats.score(&[1, 2], 40), 1_348_537);
        assert_eq!(stats.score(&[1, 0], 51), 334_880);
        assert_eq!(stats.score(&[0, 0], 28), 0);
    }

    #[test]
    fn rarer_terms_and_shorter_documents_score_higher() {
        let mut stats = Stats::new(2);
        for (counts, length) in [(&[1, 0][..], 10), (&[1, 0], 10), (&[1, 1], 10)] {
            stats.add(counts, length);
        }
        assert!(stats.score(&[0, 1], 10) > stats.score(&[1, 0], 10));
        assert!(stats.score(&[1, 0], 5) > stats.score(&[1, 0], 20));
    }
}
