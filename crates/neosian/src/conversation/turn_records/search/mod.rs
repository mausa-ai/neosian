//! A record search as every store takes it (DESIGN §40.54 item 11, T1b2a2):
//! the request and its rules, what a record is matched on and counted by,
//! its excerpt by memory search's rule, and a hit in item 11's key order.
//! The record is the unit: terms are memory search's (whitespace split,
//! folded a scalar at a time), a record matches on its text and a call on
//! its name too, so an occurrence spanning two records is not found and
//! turn-level matching stays with `search_turns`. Ranking is in `rank.rs`.

mod rank;

pub use rank::{RecordKey, RecordRanking};

use serde_json::{Map, Value, json};

use super::{Kind, TurnRecord};
use crate::conversation::rank::{Bounds, Order, bounds};
use crate::memory::search::matching::{lower, terms, window};
use crate::store::time::Stamp;

/// The kinds a search can select, in item 3.2's order; the default.
pub const SEARCHABLE: [Kind; 5] = [
    Kind::Prompt,
    Kind::Reply,
    Kind::System,
    Kind::ToolResult,
    Kind::ToolCall,
];

/// One record search, its rules checked.
#[derive(Clone, Debug)]
pub struct RecordSearch {
    /// Distinct folded terms, ascending: the order counts follow.
    pub terms: Vec<String>,
    /// None: every conversation; empty: no hits.
    pub conversations: Option<Vec<String>>,
    /// The kinds selected, distinct, in the order given; empty selects none.
    pub kinds: Vec<Kind>,
    pub order: Order,
    pub bounds: Bounds,
    pub limit: usize,
}

impl RecordSearch {
    /// Item 11's request rules: memory search's query bounds, searchable
    /// kinds only, `limit` 1 to 100, the time bounds as history ranking
    /// reads them.
    pub fn new(
        query: &str,
        kinds: Option<Vec<Kind>>,
        order: Order,
        since: Option<String>,
        before: Option<String>,
        limit: usize,
    ) -> Result<Self, String> {
        let count = query.split_whitespace().count();
        if !(1..=1024).contains(&query.chars().count()) || !(1..=16).contains(&count) {
            return Err("query requires 1..1024 characters and 1..16 whitespace terms".into());
        }
        let mut chosen = Vec::new();
        for kind in kinds.unwrap_or_else(|| SEARCHABLE.to_vec()) {
            if !kind.searchable() {
                return Err(format!("kind {:?} is not searchable", kind.as_str()));
            }
            if !chosen.contains(&kind) {
                chosen.push(kind);
            }
        }
        if !(1..=100).contains(&limit) {
            return Err("limit must be between 1 and 100".into());
        }
        Ok(Self {
            terms: terms(query),
            conversations: None,
            kinds: chosen,
            order,
            bounds: bounds(since.as_deref(), before.as_deref())?,
            limit,
        })
    }
}

/// What a record matches on, folded: its text, and before it a call's name
/// (`name text`, as history search matches a call; a term holds no white
/// space, so none spans the two).
fn matched(record: &TurnRecord) -> (Option<String>, String) {
    let name = record
        .name
        .as_deref()
        .filter(|_| record.kind == Kind::ToolCall)
        .map(lower);
    (name, lower(&record.text))
}

/// A record's per-term non-overlapping counts and its folded length in
/// scalars, as memory ranking version 1 takes them.
pub fn record_facts(record: &TurnRecord, terms: &[String]) -> (Vec<u32>, u64) {
    let (name, text) = matched(record);
    let count = |folded: &str, term: &str| folded.matches(term).count();
    let counts = terms
        .iter()
        .map(|term| {
            let found = name.as_deref().map_or(0, |name| count(name, term)) + count(&text, term);
            u32::try_from(found).unwrap_or(u32::MAX)
        })
        .collect();
    let length = name.map_or(0, |name| name.chars().count() as u64 + 1);
    (counts, length + text.chars().count() as u64)
}

/// A record's excerpt by memory search's rule: at most 240 scalars of its
/// text, starting up to 40 before the first match there, and that start in
/// scalars; from the start when only a call's name matched.
pub fn record_excerpt(text: &str, terms: &[String]) -> (usize, String) {
    let folded = lower(text);
    let first = terms
        .iter()
        .filter_map(|term| folded.find(term.as_str()))
        .min();
    window(text, first.unwrap_or(0))
}

/// One hit, as item 11 answers it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RecordHit {
    pub conversation_id: String,
    pub turn: u64,
    pub seq: u64,
    pub message: u64,
    pub kind: Kind,
    pub call_id: Option<String>,
    /// A call's own name, or a tool result's call's, found in its turn.
    pub name: Option<String>,
    pub created_at: Stamp,
    pub actor: Option<String>,
    pub size: u64,
    /// The excerpt's offset in scalars and its text.
    pub excerpt: (usize, String),
    pub score: Option<u64>,
}

impl RecordHit {
    /// The hit in item 11's key order, `score` under relevance alone.
    pub fn to_json(&self) -> Value {
        let mut map = Map::new();
        map.insert("conversation_id".into(), json!(self.conversation_id));
        map.insert("turn".into(), json!(self.turn));
        map.insert("seq".into(), json!(self.seq));
        map.insert("message".into(), json!(self.message));
        map.insert("kind".into(), json!(self.kind.as_str()));
        map.insert("call_id".into(), json!(self.call_id));
        map.insert("name".into(), json!(self.name));
        map.insert("created_at".into(), json!(self.created_at.z()));
        map.insert("actor".into(), json!(self.actor));
        map.insert("size".into(), json!(self.size));
        let (offset, text) = &self.excerpt;
        map.insert("excerpt".into(), json!({"offset": offset, "text": text}));
        if let Some(score) = self.score {
            map.insert("score".into(), json!(score));
        }
        Value::Object(map)
    }
}
