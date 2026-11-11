//! Matched passages for memory passages version 1 (DESIGN §40.42).
//!
//! Every occurrence in a long content or metadata value proposes a window
//! starting 40 scalars before it. Windows holding more distinct terms, then
//! more occurrences, win; chosen windows never overlap within a value.

use super::Passage;

const WIDTH: usize = 240;
const LEAD: usize = 40;

struct Window {
    value: usize,
    start: usize,
    end: usize,
    distinct: u32,
    occurrences: usize,
}

/// (start scalar, end scalar exclusive, term bit) of every non-overlapping
/// occurrence, located in the folded text and mapped back to original scalars.
fn occurrences(text: &str, terms: &[String]) -> Vec<(usize, usize, u32)> {
    let mut total = 0;
    let ends: Vec<usize> = text
        .chars()
        .map(|ch| {
            total += ch.to_lowercase().map(char::len_utf8).sum::<usize>();
            total
        })
        .collect();
    let folded: String = text.chars().flat_map(char::to_lowercase).collect();
    let mut found = Vec::new();
    for (index, term) in terms.iter().enumerate() {
        for (at, matched) in folded.match_indices(term.as_str()) {
            let start = ends.partition_point(|&e| e <= at);
            let stop = (ends.partition_point(|&e| e < at + matched.len()) + 1).min(ends.len());
            found.push((start, stop, 1_u32 << index));
        }
    }
    found.sort_unstable();
    found
}

fn windows(value: usize, text: &str, terms: &[String], out: &mut Vec<Window>) {
    let length = text.chars().count();
    if length <= WIDTH {
        return;
    }
    let found = occurrences(text, terms);
    let mut starts: Vec<usize> = found.iter().map(|o| o.0.saturating_sub(LEAD)).collect();
    starts.dedup();
    let mut first = 0;
    for start in starts {
        let end = (start + WIDTH).min(length);
        while first < found.len() && found[first].0 < start {
            first += 1;
        }
        let (mut bits, mut occurrences) = (0_u32, 0);
        for occurrence in found[first..].iter().take_while(|o| o.0 < end) {
            if occurrence.1 <= end {
                bits |= occurrence.2;
                occurrences += 1;
            }
        }
        out.push(Window {
            value,
            start,
            end,
            distinct: bits.count_ones(),
            occurrences,
        });
    }
}

/// Up to `count` passages over the selected values in version 1 order.
pub(super) fn select(
    values: &[(&'static str, Option<&str>, &str)],
    terms: &[String],
    count: usize,
) -> Vec<Passage> {
    let mut all = Vec::new();
    for (index, (field, _, text)) in values.iter().enumerate() {
        if *field != "path" {
            windows(index, text, terms, &mut all);
        }
    }
    all.sort_by(|a, b| {
        (b.distinct, b.occurrences, a.value, a.start).cmp(&(
            a.distinct,
            a.occurrences,
            b.value,
            b.start,
        ))
    });
    let mut chosen: Vec<&Window> = Vec::new();
    for window in &all {
        if chosen.len() == count {
            break;
        }
        let overlaps = chosen
            .iter()
            .any(|c| c.value == window.value && window.start < c.end && c.start < window.end);
        if !overlaps {
            chosen.push(window);
        }
    }
    chosen.sort_by_key(|w| (w.value, w.start));
    let mut headings = None;
    chosen
        .into_iter()
        .map(|w| {
            let (field, key, text) = values[w.value];
            let heading = (field == "content").then(|| {
                let found = headings.get_or_insert_with(|| super::headings::scan(text));
                super::headings::before(found, w.start)
            });
            Passage {
                field: field.into(),
                metadata_key: key.map(str::to_string),
                offset: w.start,
                text: text.chars().skip(w.start).take(w.end - w.start).collect(),
                heading: heading.flatten(),
            }
        })
        .collect()
}
