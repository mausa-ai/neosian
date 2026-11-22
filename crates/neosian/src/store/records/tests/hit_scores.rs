//! Relevance over records on a FileStore (§40.54 item 11, T1b2a2): every
//! candidate record scored by memory ranking version 1, the formula worked
//! out here apart from the code, ties in newest order.

use serde_json::json;

use super::hits::{mixed, search};
use crate::conversation::rank::Order;

/// BM25 as memory ranking version 1 states it, computed here apart from
/// the code: k1 1.2, b 0.75, statistics over the candidate records.
fn bm25(candidates: &[(&[u32], u64)], counts: &[u32], length: u64) -> u64 {
    let documents = candidates.len() as f64;
    let average = candidates.iter().map(|c| c.1 as f64).sum::<f64>() / documents;
    let mut total = 0.0;
    for (term, count) in counts.iter().enumerate() {
        if *count == 0 {
            continue;
        }
        let frequency = candidates.iter().filter(|c| c.0[term] > 0).count() as f64;
        let idf = (1.0 + (documents - frequency + 0.5) / (frequency + 0.5)).ln();
        let tf = f64::from(*count);
        total += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length as f64 / average));
    }
    (total * 1e6).round_ties_even() as u64
}

#[test]
fn relevance_scores_records_by_memory_ranking_version_1() {
    let (_home, store) = mixed("records-hits-relevance");
    // Terms ascending: alpha, grep. Lengths are the folded scalars; a call
    // is matched as `bash {"cmd":"grep alpha"}`.
    let candidates: [(&[u32], u64); 5] = [
        (&[1, 0], 11), // c-a 2 #0 alpha again
        (&[1, 0], 5),  // c-b 1 #0 alpha
        (&[1, 0], 22), // c-a 1 #0 find alpha in the logs
        (&[1, 1], 25), // c-a 1 #1 the call
        (&[2, 0], 24), // c-a 1 #2 alpha found twice: alpha
    ];
    let scores: Vec<u64> = candidates
        .iter()
        .map(|c| bm25(&candidates, c.0, c.1))
        .collect();
    let hits = store
        .search_records(&search("grep ALPHA", Order::Relevance))
        .unwrap();
    let got: Vec<(String, u64, u64, Option<u64>)> = hits
        .iter()
        .map(|h| (h.conversation_id.clone(), h.turn, h.seq, h.score))
        .collect();
    let mut expected = vec![
        ("c-a", 2, 0, scores[0]),
        ("c-b", 1, 0, scores[1]),
        ("c-a", 1, 0, scores[2]),
        ("c-a", 1, 1, scores[3]),
        ("c-a", 1, 2, scores[4]),
    ];
    expected.sort_by_key(|row| std::cmp::Reverse(row.3)); // stable: ties keep newest order
    let expected: Vec<(String, u64, u64, Option<u64>)> = expected
        .into_iter()
        .map(|(id, turn, seq, score)| (id.to_string(), turn, seq, Some(score)))
        .collect();
    assert_eq!(got, expected);
    assert_eq!(
        (got[0].0.as_str(), got[0].1, got[0].2),
        ("c-a", 1, 1),
        "the call holds both terms"
    );
    assert_eq!(hits[0].to_json()["score"], json!(scores[3]));
}
