//! `neosian search` (`conversation/cli_search.py`, DESIGN §32): the terms
//! are the positional words, the rule the store's own, newest first;
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
use super::search::parse_query;
use crate::cli::usage;
use crate::messaging::cli_history::{Hit, search_hits};
use crate::remote::selection::Selection;
use crate::repr::repr;

const DEFAULT_LIMIT: i64 = 20;
/// The wire's page (§18.2): every substrate alike.
const LIMIT_MAX: i64 = 500;

/// Find the turns holding every term, newest first, in any conversation.
#[derive(Args)]
pub struct Search {
    /// the terms; a turn holds every one
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
    /// search this conversation only (repeatable); default: every one
    #[arg(long = "conversation", value_name = "ID")]
    conversations: Vec<String>,
    /// the newest N hits, 1 to 500 (default: 20)
    #[arg(long, default_value_t = DEFAULT_LIMIT, allow_negative_numbers = true)]
    limit: i64,
    /// one JSON object
    #[arg(long)]
    json: bool,
}

pub fn run(args: &Search, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let json = args.json;
    let selected = match Selection::resolve_schema(
        args.root.as_deref(),
        args.url.as_deref(),
        args.schema.as_deref(),
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
    let store = match selected.open() {
        Ok(store) => store,
        Err(error) => return failed(&super::Error::from(error).text(), None, json, out, err),
    };
    let ids = (!args.conversations.is_empty()).then_some(args.conversations.as_slice());
    let turns = match store.search_turns(&terms, ids, args.limit as usize) {
        Ok(turns) => turns,
        Err(error) => return failed(&error.text(), None, json, out, err),
    };
    let hits = match search_hits(store.as_ref(), &turns, &terms, ids, args.limit as usize) {
        Ok(hits) => hits,
        Err(error) => return failed(&error.text(), None, json, out, err),
    };
    if json {
        let hits: Vec<Value> = hits.iter().map(|hit| hit.json(&terms)).collect();
        let envelope = json!({
            "query": query,
            "conversations": ids,
            "limit": args.limit,
            "client": store.client(),
            "hits": hits,
        });
        writeln!(out, "{envelope}")?;
        return Ok(0);
    }
    if hits.is_empty() {
        writeln!(out, "no turn matches every term of {}", repr(&query))?;
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
