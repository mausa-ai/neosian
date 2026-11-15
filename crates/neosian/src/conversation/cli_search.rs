//! `neosian search` (`conversation/cli_search.py`, DESIGN §32): the terms
//! are the positional words, relevance by default, newest when asked (§40.43);
//! there is no `--scope`, since turns carry none, and `--conversation`
//! narrows. Exit tiers (§14.1): 2 for grammar with nothing constructed,
//! 1 when the store answers with an error, 0 with the hits, or none.

use std::io::{self, Write};
use std::path::PathBuf;

use clap::Args;
use serde_json::{Value, json};

use super::failed;
use super::hits::hit_line;
use super::ids::check_conversation_id;
use super::rank::{Order, Request};
use super::search::parse_query;
use crate::cli::usage;
use crate::messaging::cli_history::{Hit, search_hits};
use crate::remote::selection::Selection;
use crate::repr::repr;

const DEFAULT_LIMIT: i64 = 20;
/// The wire's page (§18.2): every substrate alike.
const LIMIT_MAX: i64 = 500;

/// Find turns by their words, best match first, in any conversation.
#[derive(Args)]
pub struct Search {
    /// the terms; relevance needs one, newest every one
    #[arg(required = true, value_name = "QUERY")]
    query: Vec<String>,
    /// FileStore root directory (created on start; default: the home,
    /// ~/.neosian or $NEOSIAN_HOME)
    #[arg(long)]
    root: Option<PathBuf>,
    /// State process URL; token from NEOSIAN_CLIENT_TOKEN.
    #[arg(long)]
    url: Option<String>,
    /// Postgres schema (Postgres only).
    #[arg(long)]
    schema: Option<String>,
    #[arg(long)]
    profile: Option<String>,
    /// search this conversation only (repeatable); default: every one
    #[arg(long = "conversation", value_name = "ID")]
    conversations: Vec<String>,
    /// how many hits, 1 to 500 (default: 20)
    #[arg(long, default_value_t = DEFAULT_LIMIT, allow_negative_numbers = true)]
    limit: i64,
    /// one JSON object
    #[arg(long)]
    json: bool,
    /// order: relevance (default) or newest; newest without bounds is version 1
    #[arg(long, value_enum)]
    order: Option<Order>,
    /// only turns at or after this UTC instant (RFC 3339 ending in Z)
    #[arg(long)]
    since: Option<String>,
    /// only turns before this UTC instant (RFC 3339 ending in Z): an as-of cutoff
    #[arg(long)]
    before: Option<String>,
}

const UNRANKED: &str = "the store does not support history-ranking version 1";

pub fn run(args: &Search, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let json = args.json;
    let selected = match Selection::resolve_profile(
        args.root.as_deref(),
        args.url.as_deref(),
        args.schema.as_deref(),
        args.profile.as_deref(),
        &std::env::current_dir()?,
        false,
    ) {
        Ok(selected) => selected,
        Err(message) => return usage(json, &message, out, err),
    };
    let query = args.query.join(" ");
    let terms = match parse_query(&query) {
        Ok(terms) => terms,
        Err(message) => return usage(json, &message, out, err),
    };
    if !(1..=LIMIT_MAX).contains(&args.limit) {
        let message = format!("--limit must be 1 to {LIMIT_MAX}, got {}", args.limit);
        return usage(json, &message, out, err);
    }
    for conversation in &args.conversations {
        if let Err(reason) = check_conversation_id(conversation) {
            let message = format!("--conversation {}: {reason}", repr(conversation));
            return usage(json, &message, out, err);
        }
    }
    let bounded = args.since.is_some() || args.before.is_some();
    let mut request = Request {
        terms: terms.clone(),
        ids: None,
        limit: args.limit as usize,
        order: args.order.unwrap_or(Order::Relevance),
        since: args.since.clone(),
        before: args.before.clone(),
    };
    let bounds = match request.bounds() {
        Ok(bounds) => bounds,
        Err(message) => return usage(json, &message, out, err),
    };
    let store = match selected.open() {
        Ok(store) => store,
        Err(error) => return failed(&super::Error::from(error).text(), None, json, out, err),
    };
    let discovered;
    let ids = if !args.conversations.is_empty() {
        Some(args.conversations.as_slice())
    } else if store.profile().is_some() {
        discovered = match store.conversations() {
            Ok(ids) => ids,
            Err(error) => return failed(&super::Error::from(error).text(), None, json, out, err),
        };
        Some(discovered.as_slice())
    } else {
        None
    };
    let mut fallback = None;
    if (request.order == Order::Relevance || bounded) && !store.history_ranking() {
        if args.order.is_some() || bounded {
            return failed(UNRANKED, None, json, out, err);
        }
        request.order = Order::Newest;
        fallback = Some(format!("{UNRANKED}; newest first"));
    }
    let relevance = request.order == Order::Relevance;
    let ranked_path = relevance || bounded;
    let (turns, scores): (Vec<_>, Vec<_>) = if ranked_path {
        request.ids = ids.map(<[String]>::to_vec);
        match store.rank_turns(&request) {
            Ok(ranked) => ranked.into_iter().unzip(),
            Err(error) => return failed(&error.text(), None, json, out, err),
        }
    } else {
        match store.search_turns(&terms, ids, args.limit as usize) {
            Ok(turns) => {
                let none = vec![None; turns.len()];
                (turns, none)
            }
            Err(error) => return failed(&error.text(), None, json, out, err),
        }
    };
    let score_of = |turn: &super::Turn| {
        turns
            .iter()
            .position(|t| std::ptr::eq(t, turn))
            .and_then(|i| scores[i])
    };
    let hits = match search_hits(
        store.as_ref(),
        &turns,
        &terms,
        ids,
        args.limit as usize,
        relevance,
        bounds,
    ) {
        Ok(hits) => hits,
        Err(error) => return failed(&error.text(), None, json, out, err),
    };
    if json {
        let hits: Vec<Value> = hits
            .iter()
            .map(|hit| {
                let mut value = hit.json(&terms);
                if let Hit::Turn { turn, .. } = hit
                    && let Some(score) = score_of(turn)
                {
                    value["score"] = json!(score);
                }
                value
            })
            .collect();
        let mut envelope = json!({
            "query": query,
            "conversations": ids,
            "limit": args.limit,
            "client": store.client(),
            "hits": hits,
        });
        if ranked_path || fallback.is_some() {
            envelope["order"] = json!(request.order.name());
        }
        if let Some(reason) = &fallback {
            envelope["fallback"] = json!(reason);
        }
        writeln!(out, "{envelope}")?;
        return Ok(0);
    }
    if relevance {
        writeln!(out, "order: relevance")?;
    }
    if let Some(reason) = &fallback {
        writeln!(out, "fallback: {reason}")?;
    }
    if hits.is_empty() {
        let rule = if relevance { "any" } else { "every" };
        writeln!(out, "no turn matches {rule} term of {}", repr(&query))?;
    }
    for hit in &hits {
        match hit {
            Hit::Turn { turn, annotations } => {
                writeln!(out, "{}", hit_line(turn, &terms))?;
                for note in annotations {
                    writeln!(out, "{note}")?;
                }
            }
            Hit::Message {
                annotation,
                snippet,
                ..
            } => writeln!(out, "{annotation}\n  {snippet}")?,
        }
    }
    Ok(0)
}
