//! Pinned relevance arithmetic for memory ranking version 1 (DESIGN §40.41).
//!
//! BM25 over version 1 substring occurrence counts. The statistics come from
//! the authorized, eligible selection of one scope; the score is quantized to
//! integer micro-points so independent implementations agree on order.

const K1: f64 = 1.2;
const B: f64 = 0.75;

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
    use super::Stats;

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
