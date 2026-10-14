//! `neosian search` (`conversation/cli_search.py`, DESIGN §32): the terms
//! are the positional words, the rule the store's own, newest first;
//! there is no `--scope`, since turns carry none, and `--conversation`
//! narrows. Exit tiers (§14.1): 2 for grammar with nothing constructed,
//! 1 when the store answers with an error, 0 with the hits, or none.

use std::env;
use std::io::{self, Write};
use std::path::PathBuf;

use clap::Args;
use serde_json::{Value, json};

use super::failed;
use super::hits::hit_line;
use super::ids::check_conversation_id;
use super::search::parse_query;
use crate::cli::usage;
use crate::home::home;
use crate::memory::cli::{DSN_ENV, NEEDS_PYTHON};
use crate::messaging::cli_history::{Hit, search_hits};
use crate::repr::repr;
use crate::store::FileStore;

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
    if env::var_os(DSN_ENV).is_some_and(|dsn| !dsn.is_empty()) {
        return failed(NEEDS_PYTHON, None, json, out, err);
    }
    let root = match &args.root {
        Some(root) => root.clone(),
        None => home()?,
    };
    let store = FileStore::open(&root)?;
    let ids = (!args.conversations.is_empty()).then_some(args.conversations.as_slice());
    let turns = match store.search_turns(&terms, ids, args.limit as usize) {
        Ok(turns) => turns,
        Err(error) => return failed(&error.text(), None, json, out, err),
    };
    let hits = match search_hits(&store, &turns, &terms, ids, args.limit as usize) {
        Ok(hits) => hits,
        Err(error) => return failed(&error.text(), None, json, out, err),
    };
    if json {
        let hits: Vec<Value> = hits.iter().map(|hit| hit.json(&terms)).collect();
        let envelope = json!({
            "query": query,
            "conversations": ids,
            "limit": args.limit,
            "client": Value::Null,
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
