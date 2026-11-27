//! Record search at the agent doors (DESIGN §40.54 items 11, 15.2 and 19,
//! #448): a hit names one record. On a store that keeps records a door asks
//! the store's routed `search_records` with the original query, the door's
//! own order, the bounds, the kinds and the conversations the caller named;
//! a profile then limits the search to the conversations its history
//! admits. On a store without records, explicit `kinds` are applied to the
//! turns a turn search returned, one turn at a time, and the answer says it
//! is an approximation. A hit's excerpt is the store's answer (on that
//! fallback, the matcher's window over a returned turn); no hit reads a
//! record whole.

use super::Turn;
use super::rank::Order;
use super::turn_records::{
    Kind, RecordHit, RecordSearch, record_excerpt, record_facts, records_of,
};
use crate::assets::{TOOLS, quoted};
use crate::remote::records::RecordQuery;
use crate::repr::repr;
use crate::state::records::{Fault, Records};
use crate::text::flatten;

#[cfg(test)]
mod tests;

/// The most hits one record answer holds (item 11's `limit`).
pub const RECORD_LIMIT: usize = 100;

/// The kinds a door accepts, as item 19 lists them.
const NAMES: &str = "prompt, reply, tool_call, tool_result and system";

/// The kinds a caller named, checked: the five searchable kinds only, each
/// once in the order given. Reasoning, attachments and other opaque kinds
/// are refused by name, and so is an empty list.
pub fn parse_kinds<'a>(names: impl IntoIterator<Item = &'a str>) -> Result<Vec<Kind>, String> {
    let mut kinds = Vec::new();
    for name in names {
        let kind = match Kind::parse(name) {
            Some(kind) if kind.searchable() => kind,
            Some(_) => {
                return Err(format!(
                    "kind {} is never searched; kinds takes {NAMES}",
                    repr(name)
                ));
            }
            None => return Err(format!("unknown kind {}; kinds takes {NAMES}", repr(name))),
        };
        if !kinds.contains(&kind) {
            kinds.push(kind);
        }
    }
    if kinds.is_empty() {
        return Err(format!("kinds needs at least one of {NAMES}"));
    }
    Ok(kinds)
}

/// The kinds as an answer names them.
pub fn kind_names(kinds: &[Kind]) -> String {
    let names: Vec<&str> = kinds.iter().map(|kind| kind.as_str()).collect();
    names.join(", ")
}

/// One record search as a door asks it.
#[derive(Clone, Debug)]
pub struct Asked {
    /// The query as the caller wrote it.
    pub query: String,
    /// The conversations the caller named; `None` for every one the
    /// caller may read.
    pub conversations: Option<Vec<String>>,
    /// `None` for item 11's default, the five searchable kinds.
    pub kinds: Option<Vec<Kind>>,
    pub order: Order,
    pub since: Option<String>,
    pub before: Option<String>,
    pub limit: usize,
}

impl Asked {
    /// The request under item 11's rules, checked before anything is
    /// asked: the matcher the fallback applies to returned turns.
    pub fn check(&self) -> Result<RecordSearch, String> {
        let (since, before) = (self.since.clone(), self.before.clone());
        let mut search = RecordSearch::new(
            &self.query,
            self.kinds.clone(),
            self.order,
            since,
            before,
            self.limit,
        )?;
        search.conversations = self.conversations.clone();
        Ok(search)
    }

    /// Every hit of the routed record search, the order always stated. A
    /// refusal stays a refusal: nothing is asked again.
    pub fn search(&self, records: &dyn Records) -> Result<Vec<RecordHit>, Fault> {
        records.search(&RecordQuery {
            query: self.query.clone(),
            conversations: self.conversations.clone(),
            kinds: self.kinds.clone(),
            order: Some(self.order),
            since: self.since.clone(),
            before: self.before.clone(),
            limit: Some(self.limit),
        })
    }
}

/// The record hits one returned turn holds, by the matcher of `search`,
/// added after `hits` until `search.limit`: a record of a selected kind
/// holding every term (newest) or a term (relevance), its excerpt by memory
/// search's rule, a tool result named by its call in the same turn, and no
/// score, since a turn's score is not a record's. A turn the records cannot
/// carry is refused before any hit is added.
pub fn derive(turn: &Turn, search: &RecordSearch, hits: &mut Vec<RecordHit>) -> Result<(), String> {
    let records = records_of(&turn.messages)?;
    let named = |call_id: &str| {
        records
            .iter()
            .find(|record| {
                record.kind == Kind::ToolCall && record.call_id.as_deref() == Some(call_id)
            })
            .and_then(|record| record.name.clone())
    };
    for record in &records {
        if hits.len() >= search.limit {
            break;
        }
        if !search.kinds.contains(&record.kind) {
            continue;
        }
        let (counts, _) = record_facts(record, &search.terms);
        let holds = match search.order {
            Order::Newest => counts.iter().all(|count| *count > 0),
            Order::Relevance => counts.iter().any(|count| *count > 0),
        };
        if !holds {
            continue;
        }
        let name = match record.kind {
            Kind::ToolCall => record.name.clone(),
            Kind::ToolResult => record.call_id.as_deref().and_then(named),
            _ => None,
        };
        hits.push(RecordHit {
            conversation_id: turn.conversation_id.clone(),
            turn: turn.turn,
            seq: record.seq,
            message: record.message,
            kind: record.kind,
            call_id: record.call_id.clone(),
            name,
            created_at: turn.created_at,
            actor: turn.actor.clone(),
            size: record.size,
            excerpt: record_excerpt(&record.text, &search.terms),
            score: None,
        });
    }
    Ok(())
}

/// A hit's header (item 19): `[conversation #turn.seq kind name]`, the
/// turn's time and actor.
pub fn header(hit: &RecordHit) -> String {
    let name = hit
        .name
        .as_deref()
        .map(|name| format!(" {name}"))
        .unwrap_or_default();
    format!(
        "[{} #{}.{} {}{name}] {} {}",
        hit.conversation_id,
        hit.turn,
        hit.seq,
        hit.kind.as_str(),
        hit.created_at.z(),
        hit.actor.as_deref().unwrap_or("-")
    )
}

/// A hit's excerpt on one line, white space folded, `…` before it when
/// the record's text starts earlier.
pub fn excerpt(hit: &RecordHit) -> String {
    let (offset, text) = &hit.excerpt;
    let flat = flatten(text);
    if *offset > 0 {
        format!("… {flat}")
    } else {
        flat
    }
}

/// The guidance for a refusal past the candidate bound, from a local store
/// or inside a server's refusal.
pub fn too_broad(text: &str, key: &str) -> Option<String> {
    text.contains(crate::limits::BROAD)
        .then(|| quoted(TOOLS, key).expect("tools.yaml carries the search guidance"))
}
