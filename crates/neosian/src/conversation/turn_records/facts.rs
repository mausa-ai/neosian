//! A turn's search facts from its records, a record at a time (§40.54 item
//! 15.1, T1b2a1 note): each term's count in the turn's searchable text, as
//! `search::messages_text` renders it and `rank::Ranking::offer` folds and
//! counts it (non-overlapping, leftmost first), and the folded length; so a
//! turn too long to answer whole still counts in a search's statistics
//! without being held. The text is rebuilt as it passes: a message's text
//! (its text blocks joined by line feeds) when not empty, each call as its
//! name, a space and its arguments, the pieces joined by line feeds.
//! Characters fold one at a time, which differs from a whole text's fold in
//! one place: a word-final capital sigma folds to `σ` here, `ς` there.

use super::{Kind, TurnRecord};

/// One term's matcher: its characters and their failure links.
struct Matcher {
    term: Vec<char>,
    links: Vec<usize>,
    at: usize,
    count: u32,
}

impl Matcher {
    fn new(term: &str) -> Self {
        let term: Vec<char> = term.chars().collect();
        let mut links = vec![0; term.len()];
        let mut back = 0;
        for at in 1..term.len() {
            while back > 0 && term[at] != term[back] {
                back = links[back - 1];
            }
            if term[at] == term[back] {
                back += 1;
            }
            links[at] = back;
        }
        Self {
            term,
            links,
            at: 0,
            count: 0,
        }
    }

    fn step(&mut self, ch: char) {
        while self.at > 0 && self.term[self.at] != ch {
            self.at = self.links[self.at - 1];
        }
        if self.term[self.at] == ch {
            self.at += 1;
        }
        if self.at == self.term.len() {
            self.count = self.count.saturating_add(1);
            self.at = 0;
        }
    }
}

/// The facts of one turn, fed its records in `seq` order.
pub struct TextFacts {
    matchers: Vec<Matcher>,
    length: u64,
    pieces: u64,
    fresh: bool,
    message: Option<u64>,
    text_blocks: u64,
    emitting: bool,
}

impl TextFacts {
    pub fn new(terms: &[String]) -> Self {
        Self {
            matchers: terms.iter().map(|term| Matcher::new(term)).collect(),
            length: 0,
            pieces: 0,
            fresh: false,
            message: None,
            text_blocks: 0,
            emitting: false,
        }
    }

    fn fold(&mut self, ch: char) {
        for low in ch.to_lowercase() {
            self.length += 1;
            for matcher in &mut self.matchers {
                matcher.step(low);
            }
        }
    }

    /// One character of the current piece; the separator first when it
    /// opens a piece after another.
    fn emit(&mut self, ch: char) {
        if self.fresh {
            if self.pieces > 0 {
                self.fold('\n');
            }
            self.pieces += 1;
            self.fresh = false;
        }
        self.fold(ch);
    }

    fn begin(&mut self) {
        self.fresh = true;
    }

    pub fn push(&mut self, record: &TurnRecord) {
        if self.message != Some(record.message) {
            self.message = Some(record.message);
            self.text_blocks = 0;
        }
        if record.chunk == 0 {
            let own = Kind::of_role(record.role);
            self.emitting = true;
            match record.kind {
                kind if kind == own && record.block.is_none() => self.begin(),
                kind if kind == own => {
                    if self.text_blocks == 0 {
                        self.begin();
                    } else {
                        self.emit('\n');
                    }
                    self.text_blocks += 1;
                }
                Kind::ToolCall => {
                    self.begin();
                    for ch in record.name.as_deref().unwrap_or_default().chars() {
                        self.emit(ch);
                    }
                    self.emit(' ');
                }
                _ => self.emitting = false,
            }
        }
        if self.emitting {
            for ch in record.text.chars() {
                self.emit(ch);
            }
        }
    }

    /// Each term's count, in the order given, and the folded length.
    pub fn finish(self) -> (Vec<u32>, u64) {
        (self.matchers.iter().map(|m| m.count).collect(), self.length)
    }
}
