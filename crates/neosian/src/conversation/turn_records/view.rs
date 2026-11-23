//! One turn's records, in order, as the items a read answers (§40.54 item
//! 10): the kinds selected, a long piece of a kind in `heads` as one record
//! holding its head and tail, a piece fetch stopping at its piece's end.
//! A head is collected as its chunks pass, keeping only enough text for the
//! head and a window of the last chunks for the tail; a piece short enough
//! that it might fit whole is held (at most 65,534 bytes of text, with each
//! chunk's place) until its encoding is known. Each item comes with the
//! record the next item would start at.

use std::collections::VecDeque;

use super::read::{ReadItem, RecordRead, head, tail};
use super::{TurnRecord, encoded_len};

struct Collect {
    first: TurnRecord,
    limit: usize,
    long: bool,
    filled: u64,
    last_seq: u64,
    prefix: String,
    prefix_bytes: usize,
    window: VecDeque<String>,
    window_bytes: usize,
    held: Vec<(u64, u64, String)>,
}

impl Collect {
    fn new(first: &TurnRecord, limit: usize) -> Self {
        Self {
            first: first.clone(),
            limit,
            long: first.size.saturating_add(2) > 2 * limit as u64,
            filled: 0,
            last_seq: first.seq,
            prefix: String::new(),
            prefix_bytes: 0,
            window: VecDeque::new(),
            window_bytes: 0,
            held: Vec::new(),
        }
    }

    fn add(&mut self, record: &TurnRecord) {
        self.filled += record.text.len() as u64;
        self.last_seq = record.seq;
        if !self.long {
            self.held
                .push((record.seq, record.chunk, record.text.clone()));
            return;
        }
        let bytes = encoded_len(&record.text) - 2;
        if self.prefix_bytes < self.limit {
            self.prefix.push_str(&record.text);
            self.prefix_bytes += bytes;
        }
        self.window.push_back(record.text.clone());
        self.window_bytes += bytes;
        while self.window.len() > 1 {
            let front = encoded_len(&self.window[0]) - 2;
            if self.window_bytes - front < self.limit {
                break;
            }
            self.window.pop_front();
            self.window_bytes -= front;
        }
    }

    fn complete(&self) -> bool {
        self.filled >= self.first.size
    }

    fn finish(self, turn: u64) -> Vec<(ReadItem, u64)> {
        let next = self.last_seq.saturating_add(1);
        let whole: Option<String> =
            (!self.long).then(|| self.held.iter().map(|(_, _, text)| text.as_str()).collect());
        if whole
            .as_ref()
            .is_some_and(|text| encoded_len(text) <= 2 * self.limit)
        {
            let first = self.first;
            return self
                .held
                .into_iter()
                .map(|(seq, chunk, text)| {
                    let content = first.content.filter(|_| chunk == 0);
                    let record = TurnRecord {
                        seq,
                        chunk,
                        text,
                        content,
                        ..first.clone()
                    };
                    (plain(turn, record), seq.saturating_add(1))
                })
                .collect();
        }
        let (front, back) = match &whole {
            Some(text) => (
                head(text, self.limit).to_string(),
                tail(text, self.limit).to_string(),
            ),
            None => {
                let window: String = self.window.iter().map(String::as_str).collect();
                (
                    head(&self.prefix, self.limit).to_string(),
                    tail(&window, self.limit).to_string(),
                )
            }
        };
        let record = TurnRecord {
            chunk: 0,
            text: front,
            ..self.first
        };
        vec![(
            ReadItem::Record {
                turn,
                record,
                tail: Some(back),
                truncated: true,
            },
            next,
        )]
    }
}

fn plain(turn: u64, record: TurnRecord) -> ReadItem {
    ReadItem::Record {
        turn,
        record,
        tail: None,
        truncated: false,
    }
}

/// One turn's view: the read, the record it starts at, whether it fetches
/// one piece.
pub struct Viewer<'r> {
    read: &'r RecordRead,
    turn: u64,
    start: u64,
    seen: bool,
    done: bool,
    collect: Option<Collect>,
}

impl<'r> Viewer<'r> {
    pub fn new(read: &'r RecordRead, turn: u64, start: u64) -> Self {
        Self {
            read,
            turn,
            start,
            seen: false,
            done: false,
            collect: None,
        }
    }

    /// A piece fetch has answered its piece.
    pub fn done(&self) -> bool {
        self.done
    }

    /// The next record of the turn, in `seq` order: the items it completes.
    pub fn push(&mut self, record: &TurnRecord) -> Vec<(ReadItem, u64)> {
        if self.done || record.seq < self.start {
            return Vec::new();
        }
        let first = !self.seen;
        self.seen = true;
        if self.read.piece && !first && record.chunk == 0 {
            self.done = true;
            return Vec::new();
        }
        if let Some(collect) = self.collect.as_mut() {
            collect.add(record);
            if collect.complete() {
                return self.collect.take().expect("collecting").finish(self.turn);
            }
            return Vec::new();
        }
        if !self.read.selects(record.kind) {
            return Vec::new();
        }
        let limit = self
            .read
            .head(record.kind)
            .filter(|_| record.chunk == 0 && !self.read.piece);
        if let Some(limit) = limit {
            let mut collect = Collect::new(record, limit);
            collect.add(record);
            if collect.complete() {
                return collect.finish(self.turn);
            }
            self.collect = Some(collect);
            return Vec::new();
        }
        vec![(
            plain(self.turn, record.clone()),
            record.seq.saturating_add(1),
        )]
    }
}
