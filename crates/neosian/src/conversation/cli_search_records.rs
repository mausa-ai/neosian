//! `neosian search` naming records (DESIGN §40.54 items 15.2 and 19, #448).
//! On a store that keeps records every search is a record search through
//! the store's routed `search_records`, at most 100 hits; on a store
//! without records `--kind` derives record hits from the turns the turn
//! search returns and says so. Text gives one line per record hit, its
//! header and excerpt; JSON gives item 11's keys per record hit, typed
//! `record`, beside the typed message hits a search without `--kind` keeps.
//! A refusal of the record search is reported as it came and never asked
//! again as a turn search. Exit tiers as `cli_search.rs`.

use std::io::{self, Write};

use serde_json::{Map, Value, json};

use super::cli_search::{UNRANKED, each_turn, turn_order};
use super::failed;
use super::rank::{Bounds, Order, Request};
use super::record_search::{Asked, RECORD_LIMIT, derive, excerpt, header, kind_names, too_broad};
use super::turn_records::{Kind, RecordHit};
use crate::assets::{TOOLS, quoted, render};
use crate::cli::usage;
use crate::messaging::cli_history::{Hit, message_hit};
use crate::messaging::mailbox::Mailbox;
use crate::messaging::record_history::{Beside, Row, rows};
use crate::repr::repr;
use crate::state::State;
use crate::state::records::Records;

/// One search as the shell asked it, its grammar already checked.
pub struct Door<'a> {
    pub store: &'a dyn State,
    pub query: &'a str,
    /// The terms as the version 1 query parser gives them.
    pub terms: &'a [String],
    /// The conversations named with `--conversation`, as the record search
    /// is sent them.
    pub named: Option<Vec<String>>,
    /// The conversations a turn search and a message hit are limited to.
    pub ids: Option<&'a [String]>,
    pub limit: i64,
    pub order: Option<Order>,
    pub since: Option<String>,
    pub before: Option<String>,
    pub bounds: Bounds,
    pub kinds: Option<Vec<Kind>>,
    pub json: bool,
}

/// The refusal of a limit past a record answer's.
pub fn over_limit(limit: i64) -> String {
    format!("--limit must be 1 to {RECORD_LIMIT} for record hits, got {limit}")
}

struct Mode {
    order: Order,
    derived: bool,
    fallback: Option<String>,
    unsplit: usize,
}

impl Door<'_> {
    fn asked(&self, order: Order) -> Asked {
        Asked {
            query: self.query.to_string(),
            conversations: self.named.clone(),
            kinds: self.kinds.clone(),
            order,
            since: self.since.clone(),
            before: self.before.clone(),
            limit: self.limit as usize,
        }
    }
}

/// The record answer: from the store's records when it keeps them, else
/// derived from the turn search's turns (only with `--kind`).
pub fn run(
    door: &Door,
    records: Option<&dyn Records>,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<u8> {
    let json = door.json;
    if door.limit > RECORD_LIMIT as i64 {
        return usage(json, &over_limit(door.limit), out, err);
    }
    let asked = door.asked(door.order.unwrap_or(Order::Relevance));
    let mut search = match asked.check() {
        Ok(search) => search,
        Err(message) => return usage(json, &message, out, err),
    };
    if let Some(records) = records {
        return match asked.search(records) {
            Ok(hits) => {
                let mode = Mode {
                    order: asked.order,
                    derived: false,
                    fallback: None,
                    unsplit: 0,
                };
                answer(door, &hits, &mode, out, err)
            }
            Err(fault) => {
                let text = fault.into_error().text();
                let guidance = too_broad(&text, "search_history_too_broad_records_cli");
                failed(&text, guidance.as_deref(), json, out, err)
            }
        };
    }
    let mut request = Request {
        terms: door.terms.to_vec(),
        ids: door.ids.map(<[String]>::to_vec),
        limit: door.limit as usize,
        order: asked.order,
        since: door.since.clone(),
        before: door.before.clone(),
    };
    let fallback = match turn_order(door.store, &mut request, door.order.is_some()) {
        Ok(fallback) => fallback,
        Err(()) => return failed(UNRANKED, None, json, out, err),
    };
    search.order = request.order;
    let ranked = request.order == Order::Relevance || door.since.is_some() || door.before.is_some();
    let (mut hits, mut unsplit) = (Vec::new(), 0);
    let walked = each_turn(door.store, &request, ranked, &mut |turn, _| {
        if derive(&turn, &search, &mut hits).is_err() {
            unsplit += 1;
        }
        Ok(hits.len() < search.limit)
    });
    if let Err((text, guidance)) = walked {
        return failed(&text, guidance.as_deref(), json, out, err);
    }
    let mode = Mode {
        order: request.order,
        derived: true,
        fallback,
        unsplit,
    };
    answer(door, &hits, &mode, out, err)
}

fn line(key: &str, values: &[(&str, &str)]) -> String {
    let template = quoted(TOOLS, key).expect("tools.yaml carries the record search wording");
    render(&template, values)
}

/// A record hit in JSON: typed, item 11's keys, its annotations.
fn record_json(hit: &RecordHit, notes: &[String]) -> Value {
    let mut map = Map::new();
    map.insert("type".into(), json!("record"));
    if let Value::Object(keys) = hit.to_json() {
        map.extend(keys);
    }
    if !notes.is_empty() {
        map.insert("annotations".into(), json!(notes));
    }
    Value::Object(map)
}

fn answer(
    door: &Door,
    hits: &[RecordHit],
    mode: &Mode,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<u8> {
    let json = door.json;
    let scopes = match door.store.scopes() {
        Ok(scopes) => scopes,
        Err(error) => return failed(&super::Error::from(error).text(), None, json, out, err),
    };
    let mailbox = (!scopes.is_empty()).then(|| Mailbox::over_scopes(door.store, scopes));
    let relevance = mode.order == Order::Relevance;
    let beside = Beside {
        terms: door.terms,
        ids: door.ids,
        bounds: door.bounds,
        messages: door.kinds.is_none(),
        ranked: relevance,
        limit: door.limit as usize,
    };
    let rows = match rows(mailbox.as_ref(), hits, &beside) {
        Ok(rows) => rows,
        Err(error) => return failed(&error.text(), None, json, out, err),
    };
    let kinds = door.kinds.as_deref().map(kind_names);
    if json {
        let hits: Vec<Value> = rows
            .iter()
            .map(|row| match row {
                Row::Record { hit, notes } => record_json(hit, notes),
                Row::Message { item, text } => message_hit(item, text, door.terms).json(),
            })
            .collect();
        let names: Option<Vec<&str>> = door
            .kinds
            .as_ref()
            .map(|kinds| kinds.iter().map(|kind| kind.as_str()).collect());
        let mut envelope = json!({
            "query": door.query,
            "conversations": door.ids,
            "kinds": names,
            "limit": door.limit,
            "client": door.store.client(),
            "hits": hits,
            "order": mode.order.name(),
        });
        if let Some(reason) = &mode.fallback {
            envelope["fallback"] = json!(reason);
        }
        if mode.derived {
            envelope["approximation"] = json!(line("search_history_derived", &[]));
        }
        if mode.unsplit > 0 {
            envelope["turns_left_out"] = json!(mode.unsplit);
        }
        writeln!(out, "{envelope}")?;
        return Ok(0);
    }
    if relevance {
        writeln!(out, "order: relevance")?;
    }
    if let Some(reason) = &mode.fallback {
        writeln!(out, "fallback: {reason}")?;
    }
    if let Some(kinds) = &kinds {
        writeln!(
            out,
            "kinds: {}",
            line("search_history_kinds", &[("kinds", kinds)])
        )?;
    }
    if mode.derived {
        writeln!(
            out,
            "approximation: {}",
            line("search_history_derived", &[])
        )?;
    }
    if mode.unsplit > 0 {
        let count = mode.unsplit.to_string();
        writeln!(
            out,
            "left out: {}",
            line("search_history_unsplit", &[("count", &count)])
        )?;
    }
    if rows.is_empty() {
        let rule = if relevance { "any" } else { "every" };
        writeln!(out, "no record holds {rule} term of {}", repr(door.query))?;
    }
    for row in &rows {
        match row {
            Row::Record { hit, notes } => {
                writeln!(out, "{}  {}", header(hit), excerpt(hit))?;
                for note in notes {
                    writeln!(out, "{note}")?;
                }
            }
            Row::Message { item, text } => {
                if let Hit::Message {
                    annotation,
                    snippet,
                    ..
                } = message_hit(item, text, door.terms)
                {
                    let label = line("search_history_message", &[]);
                    writeln!(out, "{label}\n{annotation}\n  {snippet}")?;
                }
            }
        }
    }
    Ok(0)
}
