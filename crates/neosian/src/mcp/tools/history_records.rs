//! `search_history` naming records (DESIGN §40.54 items 15.2 and 19, #448).
//! On a store that keeps records every call is a record search through the
//! store's routed `search_records`: relevance unless `order` says newest,
//! the bounds and kinds as asked, the conversation the caller named, and a
//! profile's history admission. On a store without records a call naming
//! `kinds` derives record hits from the turns the turn search returns and
//! says so; a call without `kinds` stays with the turn search
//! (`history.rs`). A refusal of the record search is answered as it came
//! and never asked again as a turn search.

use serde_json::{Map, Value};

use super::history::turn_failure;
use super::history_rank;
use super::{Outcome, text};
use crate::assets::{TOOLS, quoted, render};
use crate::conversation::Error;
use crate::conversation::rank::{Bounds, Order, bounds};
use crate::conversation::record_search::{
    Asked, derive, excerpt, header, kind_names, parse_kinds, too_broad,
};
use crate::conversation::turn_records::{Kind, RecordHit};
use crate::mcp::server::Server;
use crate::messaging::history::{annotations_frame, message_block};
use crate::messaging::mailbox::Mailbox;
use crate::messaging::record_history::{Beside, Row, rows};
use crate::repr::repr;
use crate::state::records::{Fault, Records};

/// The kinds a call names, checked; `None` when it names none.
pub fn kinds(arguments: &Map<String, Value>) -> Result<Option<Vec<Kind>>, Outcome> {
    let Some(Value::Array(items)) = arguments.get("kinds") else {
        return Ok(None);
    };
    let names = items.iter().filter_map(Value::as_str);
    parse_kinds(names).map(Some).map_err(Outcome::fail)
}

/// One call of the tool, its arguments already checked by `history.rs`.
pub struct Call<'a> {
    pub server: &'a Server,
    pub arguments: &'a Map<String, Value>,
    pub query: &'a str,
    /// The terms as the version 1 query parser gives them: messages match
    /// on these.
    pub terms: &'a [String],
    /// The conversations a turn search and a message hit are limited to.
    pub ids: Option<&'a [String]>,
    pub shown: usize,
    pub kinds: Option<Vec<Kind>>,
}

/// The record answer: from the store's records when it keeps them, else
/// derived from the turn search's turns (only with `kinds`).
pub fn search(call: Call, records: Option<&dyn Records>) -> Outcome {
    let explicit = match text(call.arguments, "order") {
        None => None,
        Some("relevance") => Some(Order::Relevance),
        Some("newest") => Some(Order::Newest),
        Some(other) => {
            return Outcome::fail(format!("order must be relevance or newest; got {other:?}"));
        }
    };
    let (since, before) = (
        text(call.arguments, "since"),
        text(call.arguments, "before"),
    );
    let bounds = match bounds(since, before) {
        Ok(bounds) => bounds,
        Err(message) => return Outcome::fail(message),
    };
    let asked = Asked {
        query: call.query.to_string(),
        conversations: text(call.arguments, "conversation").map(|id| vec![id.to_string()]),
        kinds: call.kinds.clone(),
        order: explicit.unwrap_or(Order::Relevance),
        since: since.map(str::to_string),
        before: before.map(str::to_string),
        limit: call.shown,
    };
    if let Err(message) = asked.check() {
        return Outcome::fail(message);
    }
    let Some(records) = records else {
        return derived(&call, asked, bounds);
    };
    let hits = match asked.search(records) {
        Ok(hits) => hits,
        Err(fault) => return refused(fault),
    };
    let answer = Answer {
        order: asked.order,
        derived: false,
        unsplit: 0,
    };
    match answer.render(&call, &hits, bounds) {
        Ok(text) => Outcome::ok(text),
        Err(error) => Outcome::failed("search_history", &error.text()),
    }
}

/// Record hits drawn from the turns the turn search returns, under its
/// order and its refusals, and labelled as an approximation.
fn derived(call: &Call, mut asked: Asked, bounds: Bounds) -> Outcome {
    let (server, arguments) = (call.server, call.arguments);
    let ranking = match history_rank::resolve(server, arguments, call.terms, call.ids, call.shown) {
        Ok(ranking) => ranking,
        Err(outcome) => return outcome,
    };
    asked.order = ranking.order();
    let search = match asked.check() {
        Ok(search) => search,
        Err(message) => return Outcome::fail(message),
    };
    let (mut hits, mut unsplit) = (Vec::new(), 0);
    let walked = ranking.each(server, call.terms, call.ids, call.shown, &mut |turn, _| {
        if derive(&turn, &search, &mut hits).is_err() {
            unsplit += 1;
        }
        Ok(hits.len() < call.shown)
    });
    if let Err(error) = walked {
        return turn_failure(error);
    }
    let answer = Answer {
        order: ranking.order(),
        derived: true,
        unsplit,
    };
    match answer.render(call, &hits, bounds) {
        Ok(text) => Outcome::ok(ranking.frame(text)),
        Err(error) => Outcome::failed("search_history", &error.text()),
    }
}

/// A record search's refusal or failure, as it came.
fn refused(fault: Fault) -> Outcome {
    let error = fault.into_error();
    if let Error::Conversation(refused) = error {
        return Outcome::fail(format!("[{}] {}", refused.code, refused.message));
    }
    let text = error.text();
    let outcome = Outcome::failed("search_history", &text);
    match too_broad(&text, "search_history_too_broad_records") {
        Some(guidance) => outcome.hint(guidance),
        None => outcome,
    }
}

fn line(key: &str, values: &[(&str, &str)]) -> String {
    let template = quoted(TOOLS, key).expect("tools.yaml carries the record search wording");
    render(&template, values)
}

struct Answer {
    order: Order,
    derived: bool,
    unsplit: usize,
}

impl Answer {
    /// The notes, then each hit as its header with the excerpt under it and
    /// the annotations of its turn, the message hits labelled, the footer.
    fn render(&self, call: &Call, hits: &[RecordHit], bounds: Bounds) -> Result<String, Error> {
        let memory = &call.server.memory;
        let mailbox = Mailbox::of_mounts(memory.store.as_ref(), &memory.mounts, None)
            .with_actor(memory.actor.clone());
        let ranked = self.order == Order::Relevance;
        let beside = Beside {
            terms: call.terms,
            ids: call.ids,
            bounds,
            messages: call.kinds.is_none(),
            ranked,
            limit: call.shown,
        };
        let rows = rows(Some(&mailbox), hits, &beside)?;
        let mut lines = Vec::new();
        if let Some(kinds) = &call.kinds {
            let names = kind_names(kinds);
            lines.push(format!(
                "[{}]",
                line("search_history_kinds", &[("kinds", &names)])
            ));
        }
        if self.derived {
            lines.push(format!("[{}]", line("search_history_derived", &[])));
        }
        if self.unsplit > 0 {
            let count = self.unsplit.to_string();
            lines.push(format!(
                "[{}]",
                line("search_history_unsplit", &[("count", &count)])
            ));
        }
        if rows.is_empty() {
            let listed: Vec<String> = call.terms.iter().map(|term| repr(term)).collect();
            let key = if ranked {
                "search_history_record_none_ranked"
            } else {
                "search_history_record_none"
            };
            lines.push(line(key, &[("terms", &listed.join(", "))]));
            return Ok(lines.join("\n"));
        }
        let mailed = rows.iter().any(|row| match row {
            Row::Message { .. } => true,
            Row::Record { notes, .. } => !notes.is_empty(),
        });
        let blocks: Vec<String> = rows.iter().map(|row| block(row, call.terms)).collect();
        let key = if ranked {
            "search_history_record_footer_ranked"
        } else {
            "search_history_record_footer"
        };
        let footer = line(key, &[("shown", &call.shown.to_string())]);
        let gap = if mailed { "\n\n" } else { "\n" };
        lines.push(format!("{}{gap}{footer}", blocks.join(gap)));
        Ok(lines.join("\n"))
    }
}

fn block(row: &Row, terms: &[String]) -> String {
    match row {
        Row::Record { hit, notes } => {
            let mut text = format!("{}\n  {}", header(hit), excerpt(hit));
            let frame = annotations_frame(notes);
            if !frame.is_empty() {
                text.push('\n');
                text.push_str(frame.trim_end_matches('\n'));
            }
            text
        }
        Row::Message { item, text } => {
            let label = line("search_history_message", &[]);
            format!("{label}\n{}", message_block(item, text, terms))
        }
    }
}
