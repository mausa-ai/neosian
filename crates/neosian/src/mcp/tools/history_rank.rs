//! `search_history`'s order and bounds (history ranking version 1, §40.43):
//! relevance by default, newest when asked; explicit newest without bounds
//! keeps the version 1 path and output. Without the capability an implicit
//! order uses newest and says so; explicit relevance or bounds refuse.

use serde_json::{Map, Value};

use super::Outcome;
use crate::assets::{TOOLS, quoted};
use crate::conversation::Error;
use crate::conversation::hits::Found;
use crate::conversation::ids::found_in;
use crate::conversation::page::Visit;
use crate::conversation::rank::{Bounds, Order, Request};
use crate::mcp::server::Server;

pub struct Ranking {
    request: Request,
    bounded: bool,
    fallback: bool,
}

fn text<'a>(arguments: &'a Map<String, Value>, name: &str) -> Option<&'a str> {
    arguments.get(name).and_then(Value::as_str)
}

pub fn resolve(
    server: &Server,
    arguments: &Map<String, Value>,
    terms: &[String],
    ids: Option<&[String]>,
    shown: usize,
) -> Result<Ranking, Outcome> {
    let explicit = match text(arguments, "order") {
        None => None,
        Some("relevance") => Some(Order::Relevance),
        Some("newest") => Some(Order::Newest),
        Some(other) => {
            return Err(Outcome::fail(format!(
                "order must be relevance or newest; got {other:?}"
            )));
        }
    };
    let mut request = Request {
        terms: terms.to_vec(),
        ids: ids.map(<[String]>::to_vec),
        limit: shown,
        order: explicit.unwrap_or(Order::Relevance),
        since: text(arguments, "since").map(str::to_string),
        before: text(arguments, "before").map(str::to_string),
    };
    request.bounds().map_err(Outcome::fail)?;
    let bounded = request.since.is_some() || request.before.is_some();
    let mut fallback = false;
    if (request.order == Order::Relevance || bounded) && !server.memory.store.history_ranking() {
        if explicit.is_some() || bounded {
            return Err(Outcome::fail(
                "this store does not support history-ranking version 1",
            ));
        }
        request.order = Order::Newest;
        fallback = true;
    }
    Ok(Ranking {
        request,
        bounded,
        fallback,
    })
}

impl Ranking {
    pub fn relevance(&self) -> bool {
        self.request.order == Order::Relevance
    }

    /// The bounds `resolve` already checked; message hits share them.
    pub fn bounds(&self) -> Bounds {
        self.request.bounds().unwrap_or_default()
    }

    /// The order the turns come in, after any fallback.
    pub fn order(&self) -> Order {
        self.request.order
    }

    /// The hits, each built as its turn arrives; no turn is kept (§40.46).
    pub fn found(
        &self,
        server: &Server,
        terms: &[String],
        ids: Option<&[String]>,
        shown: usize,
    ) -> Result<Vec<Found>, Error> {
        let mut found = Vec::new();
        self.each(server, terms, ids, shown, &mut |turn, score| {
            found.push(Found::of(turn, terms, score));
            Ok(true)
        })?;
        Ok(found)
    }

    /// Each turn the search returns, with its score, to `visit` as it
    /// arrives, until `visit` answers false. A version 1 answer's turn of
    /// a conversation the search was not limited to is refused before it
    /// is shown; the ranked answer is checked by the store.
    pub fn each(
        &self,
        server: &Server,
        terms: &[String],
        ids: Option<&[String]>,
        shown: usize,
        visit: &mut Visit,
    ) -> Result<(), Error> {
        let store = server.memory.store.as_ref();
        if self.relevance() || self.bounded {
            store.rank_turns_each(&self.request, visit)
        } else {
            store.search_turns_each(terms, ids, shown, &mut |turn| {
                found_in(&turn, ids)?;
                visit(turn, None)
            })
        }
    }

    /// The answer, after the fallback note when the implicit order fell back.
    pub fn frame(&self, text: String) -> String {
        if self.fallback {
            let note =
                quoted(TOOLS, "search_history_fallback").expect("tools.yaml carries the note");
            format!("{note}\n{text}")
        } else {
            text
        }
    }
}
