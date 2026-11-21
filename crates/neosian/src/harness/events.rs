//! What a harness's headless output says, harness by harness reduced to
//! one vocabulary, and the bounded transcript a turn keeps of it (§40.50
//! item 8): the head and the tail, the bytes between counted, never held.

/// One thing a line of a harness's output said.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Event {
    /// The harness's own session ID.
    Session(String),
    /// Text the child agent wrote; the last one is the answer unless an
    /// `Answer` follows.
    Text(String),
    /// The harness's own final answer for the turn.
    Answer(String),
    /// A tool call, a command or a denial, as one line.
    Note(String),
    /// The harness reported that the turn failed.
    Failure(String),
}

/// The transcript's head, kept whole.
pub const HEAD_BYTES: usize = 64 * 1024;
/// The transcript's tail, the newest bytes.
pub const TAIL_BYTES: usize = 192 * 1024;
/// The most one answer returns, of new output and of the final answer.
pub const OUTPUT_BYTES: usize = 16 * 1024;

/// A prefix of `text` of at most `limit` bytes, on a character boundary.
pub fn head(text: &str, limit: usize) -> &str {
    if text.len() <= limit {
        return text;
    }
    let mut end = limit;
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    &text[..end]
}

/// A suffix of `text` of at most `limit` bytes, on a character boundary.
pub fn tail(text: &str, limit: usize) -> &str {
    if text.len() <= limit {
        return text;
    }
    let mut start = text.len() - limit;
    while !text.is_char_boundary(start) {
        start += 1;
    }
    &text[start..]
}

/// Everything a turn printed, bounded: offsets are byte positions in the
/// whole stream, so a reader's position stays valid as the middle drops.
#[derive(Default)]
pub struct Transcript {
    head: String,
    tail: String,
    total: usize,
}

impl Transcript {
    pub fn push(&mut self, text: &str) {
        self.total += text.len();
        if self.tail.is_empty() && self.head.len() + text.len() <= HEAD_BYTES {
            self.head.push_str(text);
            return;
        }
        if self.tail.is_empty() {
            // The part that still fits the head goes there; the rest starts the tail.
            let room = head(text, HEAD_BYTES - self.head.len());
            self.head.push_str(room);
            self.tail.push_str(&text[room.len()..]);
        } else {
            self.tail.push_str(text);
        }
        if self.tail.len() > TAIL_BYTES {
            let keep = tail(&self.tail, TAIL_BYTES).len();
            self.tail.drain(..self.tail.len() - keep);
        }
    }

    pub fn total(&self) -> usize {
        self.total
    }

    /// What arrived after position `from`: the newest `limit` bytes of what
    /// is still held, and how many bytes after `from` it leaves out.
    pub fn since(&self, from: usize, limit: usize) -> (String, usize) {
        let from = from.min(self.total);
        let tail_start = self.total - self.tail.len();
        let mut held = String::new();
        if from < self.head.len() {
            held.push_str(&self.head[from..]);
        }
        let start = from.max(tail_start).max(self.head.len());
        if start < self.total {
            held.push_str(&self.tail[start - tail_start..]);
        }
        let shown = tail(&held, limit).to_string();
        let skipped = self.total - from - shown.len();
        (shown, skipped)
    }
}

/// The last bytes of a stream, for a harness's stderr.
#[derive(Default)]
pub struct Tail(String);

impl Tail {
    pub const BYTES: usize = 4 * 1024;

    pub fn push(&mut self, text: &str) {
        self.0.push_str(text);
        if self.0.len() > 2 * Self::BYTES {
            let keep = tail(&self.0, Self::BYTES).len();
            self.0.drain(..self.0.len() - keep);
        }
    }

    pub fn text(&self) -> &str {
        tail(&self.0, Self::BYTES)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_reader_gets_what_is_new_and_the_count_of_what_dropped() {
        let mut transcript = Transcript::default();
        transcript.push("one\n");
        assert_eq!(transcript.since(0, OUTPUT_BYTES), ("one\n".to_string(), 0));
        let mark = transcript.total();
        transcript.push("two\n");
        assert_eq!(
            transcript.since(mark, OUTPUT_BYTES),
            ("two\n".to_string(), 0)
        );
        assert_eq!(
            transcript.since(transcript.total(), OUTPUT_BYTES),
            (String::new(), 0)
        );
        assert_eq!(transcript.since(0, 4), ("two\n".to_string(), 4));
    }

    #[test]
    fn the_middle_of_a_long_turn_is_counted_never_held() {
        let mut transcript = Transcript::default();
        let line = format!("{}é\n", "x".repeat(997));
        for _ in 0..400 {
            transcript.push(&line);
        }
        assert_eq!(transcript.total(), 400 * line.len());
        assert!(transcript.head.len() <= HEAD_BYTES && transcript.tail.len() <= TAIL_BYTES);
        let (shown, skipped) = transcript.since(0, OUTPUT_BYTES);
        assert!(shown.len() <= OUTPUT_BYTES && shown.ends_with("é\n"));
        assert_eq!(shown.len() + skipped, transcript.total());
        let (all, dropped) = transcript.since(0, usize::MAX);
        assert!(all.len() <= HEAD_BYTES + TAIL_BYTES && all.starts_with("xxx"));
        assert_eq!(all.len() + dropped, transcript.total());
        assert!(dropped > 0);
        let near_end = transcript.total() - line.len();
        assert_eq!(transcript.since(near_end, OUTPUT_BYTES), (line.clone(), 0));
    }

    #[test]
    fn cuts_fall_on_character_boundaries() {
        assert_eq!(head("aé", 2), "a");
        assert_eq!(tail("éa", 2), "a");
        let mut stderr = Tail::default();
        for _ in 0..5000 {
            stderr.push("é");
        }
        assert!(stderr.text().len() <= Tail::BYTES && stderr.text().starts_with('é'));
    }
}
