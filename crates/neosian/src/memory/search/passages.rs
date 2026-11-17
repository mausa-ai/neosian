//! Matched passages for memory passages version 1 (DESIGN §40.42).

use std::cmp::Reverse;
use std::collections::BinaryHeap;

use super::Passage;

const WIDTH: usize = 240;
const LEAD: usize = 40;
const BLOCK: usize = 4096;

/// Larger scores win. Ties go to the earlier value, then the earlier scalar.
#[derive(Eq, Ord, PartialEq, PartialOrd)]
struct Window {
    distinct: i32,
    occurrences: i32,
    location: Reverse<(usize, usize)>,
}

struct Winners {
    heap: BinaryHeap<Reverse<Window>>,
    capacity: usize,
}

impl Winners {
    fn new(count: usize) -> Self {
        // Starts are unique within a value. A chosen window can remove at most
        // 2 * WIDTH - 1 starts (itself included), all within WIDTH - 1 of it.
        // After count - 1 choices the next winner must be among this many.
        Self {
            heap: BinaryHeap::new(),
            capacity: 1 + (count - 1) * (2 * WIDTH - 1),
        }
    }

    fn consider(&mut self, window: Window) {
        if self.heap.len() < self.capacity {
            self.heap.push(Reverse(window));
        } else if let Some(mut worst) = self.heap.peek_mut()
            && window > worst.0
        {
            *worst = Reverse(window);
        }
    }
}

#[derive(Clone, Copy, Default)]
struct Change {
    distinct: i32,
    occurrences: i32,
    proposed: bool,
}

/// Score BLOCK possible starts together. Keep enough following text to finish
/// any window and any match that could propose one, even a term wider than a
/// window. `resume` carries each term's non-overlapping match phase across cuts.
fn windows(value: usize, text: &str, terms: &[String], longest: usize, winners: &mut Winners) {
    let mut source = text.chars();
    if source.clone().nth(WIDTH).is_none() {
        return;
    }
    let mut base = 0;
    let mut resume = vec![0_usize; terms.len()];
    let mut folded = String::new();
    let mut ends = Vec::new();
    let mut changes = vec![Change::default(); BLOCK + 1];
    loop {
        folded.clear();
        ends.clear();
        for ch in source.clone().take(BLOCK + WIDTH + longest) {
            folded.extend(ch.to_lowercase());
            ends.push(folded.len());
        }
        let size = ends.len().min(BLOCK);
        let cut = ends[size - 1];
        // Each scalar folds to at least one byte. With equality the scalar
        // offsets are the byte offsets, including for dense ASCII documents.
        let direct = ends.len() == folded.len();
        changes[..=size].fill(Change::default());
        for (term, skip) in terms.iter().zip(&mut resume) {
            let from = *skip;
            *skip = from.saturating_sub(cut);
            let mut covered = 0;
            for (index, _) in folded[from..].match_indices(term.as_str()) {
                let at = from + index;
                let stop = at + term.len();
                if at < cut {
                    *skip = stop.saturating_sub(cut);
                }
                let (start, end) = if direct {
                    (at, stop)
                } else {
                    (
                        ends.partition_point(|&b| b <= at),
                        ends.partition_point(|&b| b < stop) + 1,
                    )
                };
                if start >= size + WIDTH - 1 {
                    break;
                }
                let proposed = (base + start).saturating_sub(LEAD);
                if (base..base + size).contains(&proposed) {
                    changes[proposed - base].proposed = true;
                }
                // The match is wholly inside windows starting at end-WIDTH
                // through start. Add it to every such window with two deltas.
                let left = end.saturating_sub(WIDTH);
                let right = (start + 1).min(size);
                if left < right {
                    changes[left].occurrences += 1;
                    changes[right].occurrences -= 1;
                    // For distinctness count the union of this term's ranges.
                    let fresh = left.max(covered);
                    if fresh < right {
                        changes[fresh].distinct += 1;
                        changes[right].distinct -= 1;
                    }
                    covered = right;
                }
            }
        }
        let (mut distinct, mut occurrences) = (0, 0);
        for (i, change) in changes[..size].iter().enumerate() {
            distinct += change.distinct;
            occurrences += change.occurrences;
            if change.proposed {
                winners.consider(Window {
                    distinct,
                    occurrences,
                    location: Reverse((value, base + i)),
                });
            }
        }
        if ends.len() <= BLOCK {
            break;
        }
        source.nth(BLOCK - 1);
        base += BLOCK;
    }
}

/// Up to `count` passages over the selected values in version 1 order.
pub(super) fn select(
    values: &[(&'static str, Option<&str>, &str)],
    terms: &[String],
    count: usize,
) -> Vec<Passage> {
    if count == 0 {
        return Vec::new();
    }
    let longest = terms.iter().map(|t| t.chars().count()).max().unwrap_or(0);
    let mut winners = Winners::new(count);
    for (value, (field, _, text)) in values.iter().enumerate() {
        if *field != "path" {
            windows(value, text, terms, longest, &mut winners);
        }
    }

    let mut chosen: Vec<(usize, usize)> = Vec::new();
    for Reverse(window) in winners.heap.into_sorted_vec() {
        let (value, start) = window.location.0;
        if chosen
            .iter()
            .all(|&(v, s)| v != value || s.abs_diff(start) >= WIDTH)
        {
            chosen.push((value, start));
            if chosen.len() == count {
                break;
            }
        }
    }
    chosen.sort_unstable();
    let mut result: Vec<_> = chosen
        .iter()
        .map(|&(value, start)| {
            let (field, key, text) = values[value];
            Passage {
                field: field.into(),
                metadata_key: key.map(str::to_string),
                offset: start,
                text: text.chars().skip(start).take(WIDTH).collect(),
                heading: None,
            }
        })
        .collect();
    if let Some(&(value, _)) = chosen.first()
        && values[value].0 == "content"
    {
        let mut heading = super::headings::lookup(values[value].2);
        for passage in result.iter_mut().take_while(|p| p.field == "content") {
            passage.heading = heading(passage.offset);
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::select;

    #[test]
    fn ties_follow_value_order_even_when_metadata_has_earlier_offsets() {
        let content = format!("# Guide\n{}needle", ".".repeat(260));
        let notes = format!("needle{}", "x".repeat(240));
        let tag = format!("{}needle{}", "y".repeat(80), "z".repeat(180));
        let values = [
            ("content", None, content.as_str()),
            ("metadata", Some("notes"), notes.as_str()),
            ("metadata", Some("notes"), tag.as_str()),
            ("metadata", Some("tags"), notes.as_str()),
        ];
        let terms = ["needle".to_owned()];
        let passages = select(&values, &terms, 3);
        let actual: Vec<_> = passages
            .iter()
            .map(|p| {
                (
                    p.offset,
                    p.text.as_str(),
                    p.metadata_key.as_deref(),
                    p.heading.as_deref(),
                )
            })
            .collect();
        assert_eq!(
            actual,
            [
                (228, &content[228..], None, Some("Guide")),
                (0, &notes[..240], Some("notes"), None),
                (40, &tag[40..], Some("notes"), None),
            ]
        );
    }
}
