//! `neosian search` (`conversation/cli_search.py`, DESIGN §32): the terms
//! are the positional words, relevance by default, newest when asked (§40.43);
//! there is no `--scope`, since turns carry none, and `--conversation`
//! narrows. On a store that keeps records, or with `--kind`, each hit names
//! a record (`cli_search_records.rs`); otherwise turns are answered as
//! released. Exit tiers (§14.1): 2 for grammar with nothing constructed,
//! 1 when the store answers with an error, 0 with the hits, or none.

use std::io::{self, Write};
use std::path::PathBuf;

use clap::Args;
use serde_json::{Value, json};

use super::cli_search_records as records;
use super::failed;
use super::hits::{Found, hit_line};
use super::ids::{check_conversation_id, found_in};
use super::page::Visit;
use super::rank::{Order, Request};
use super::record_search::{RECORD_LIMIT, parse_kinds};
use super::search::parse_query;
use crate::cli::usage;
use crate::messaging::cli_history::{Hit, search_hits};
use crate::remote::selection::Selection;
use crate::repr::repr;
use crate::state::State;
use crate::state::records::HistoryReader;

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
    /// only records of this kind (repeatable): prompt, reply, tool_call,
    /// tool_result or system; default every one
    #[arg(long = "kind", value_name = "KIND")]
    kinds: Vec<String>,
}

pub(super) const UNRANKED: &str = "the store does not support history-ranking version 1";

/// The order a turn search takes on `store`: without history ranking an
/// implicit, unbounded relevance falls back to newest and says why; an
/// explicit relevance or a bound is refused (`Err`).
pub(super) fn turn_order(
    store: &dyn State,
    request: &mut Request,
    explicit: bool,
) -> Result<Option<String>, ()> {
    let bounded = request.since.is_some() || request.before.is_some();
    if (request.order == Order::Relevance || bounded) && !store.history_ranking() {
        if explicit || bounded {
            return Err(());
        }
        request.order = Order::Newest;
        return Ok(Some(format!("{UNRANKED}; newest first")));
    }
    Ok(None)
}

/// Each turn the turn search returns, with its score, to `visit`: ranked
/// with `request.ids` when `ranked`, else version 1's newest search, a turn
/// of a conversation it was not limited to refused. A failure as the shell
/// reports it, with guidance past the candidate bound on the ranked path.
pub(super) fn each_turn(
    store: &dyn State,
    request: &Request,
    ranked: bool,
    visit: &mut Visit,
) -> Result<(), (String, Option<String>)> {
    if ranked {
        store.rank_turns_each(request, visit).map_err(|error| {
            let text = error.text();
            let key = "search_history_too_broad_cli";
            let guidance = crate::assets::too_broad(&text, crate::assets::TOOLS, key);
            (text, guidance)
        })
    } else {
        let (terms, ids) = (&request.terms, request.ids.as_deref());
        let mut checked = |turn: super::Turn| {
            found_in(&turn, ids)?;
            visit(turn, None)
        };
        store
            .search_turns_each(terms, ids, request.limit, &mut checked)
            .map_err(|error| (error.text(), None))
    }
}

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
    let kinds = match args.kinds.as_slice() {
        [] => None,
        names => match parse_kinds(names.iter().map(String::as_str)) {
            Ok(kinds) => Some(kinds),
            Err(message) => return usage(json, &format!("--kind: {message}"), out, err),
        },
    };
    if kinds.is_some() && args.limit > RECORD_LIMIT as i64 {
        return usage(json, &records::over_limit(args.limit), out, err);
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
    let history = match HistoryReader::open(&selected) {
        Ok(history) => history,
        Err(error) => return failed(&super::Error::from(error).text(), None, json, out, err),
    };
    let store = history.state();
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
    // A store that keeps records names a record per hit; without records,
    // only --kind leaves the turn search's answer (§40.54 items 15.2, 19).
    let kept = match history.records() {
        Ok(kept) => kept,
        Err(error) => return failed(&error.text(), None, json, out, err),
    };
    if kept.is_some() || kinds.is_some() {
        let door = records::Door {
            store: store.as_ref(),
            query: &query,
            terms: &terms,
            named: (!args.conversations.is_empty()).then(|| args.conversations.clone()),
            ids,
            limit: args.limit,
            order: args.order,
            since: args.since.clone(),
            before: args.before.clone(),
            bounds,
            kinds,
            json,
        };
        return records::run(&door, kept.as_deref(), out, err);
    }
    let fallback = match turn_order(store.as_ref(), &mut request, args.order.is_some()) {
        Ok(fallback) => fallback,
        Err(()) => return failed(UNRANKED, None, json, out, err),
    };
    let relevance = request.order == Order::Relevance;
    let ranked_path = relevance || bounded;
    // Each turn becomes its hit as it arrives; the turn is not kept (§40.46).
    let mut turns: Vec<Found> = Vec::new();
    request.ids = ids.map(<[String]>::to_vec);
    let found = each_turn(store.as_ref(), &request, ranked_path, &mut |turn, score| {
        turns.push(Found::of(turn, &terms, score));
        Ok(true)
    });
    if let Err((text, guidance)) = found {
        return failed(&text, guidance.as_deref(), json, out, err);
    }
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
                let mut value = hit.json();
                if let Hit::Turn { turn, .. } = hit
                    && let Some(score) = turn.score
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
                writeln!(out, "{}", hit_line(turn))?;
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
