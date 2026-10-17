//! The history pair on the server (`conversation/recall.py`'s
//! `create_recall_any_tool`, `search_history.py`'s `create_search_any_tool`,
//! `messaging/history.py`'s `augment_history`; DESIGN §21.7, §32, §34):
//! `conversation` required on `recall_turn`, any id the store holds
//! addressable; `search_history` store-wide with `conversation`
//! narrowing; both with the mailbox's annotations merged.

use serde_json::{Map, Value};

use super::{Outcome, Tool, integer, text};
use crate::assets::{TOOLS, mapping, quoted, render};
use crate::conversation::Error;
use crate::conversation::hits::hit_block;
use crate::conversation::recall as recalling;
use crate::conversation::search::parse_query;
use crate::mcp::schema::{Field, Kind, Presence};
use crate::mcp::server::Server;
use crate::messaging::history::{augment_recall, augment_search};
use crate::messaging::mailbox::Mailbox;
use crate::repr::repr;

const MAX_HITS: i64 = 50;

const RECALL_FIELDS: [Field; 3] = [
    Field {
        name: "turn",
        kind: Kind::Int,
        presence: Presence::Required,
    },
    Field {
        name: "conversation",
        kind: Kind::Str,
        presence: Presence::Required,
    },
    Field {
        name: "call",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
];

const SEARCH_FIELDS: [Field; 3] = [
    Field {
        name: "query",
        kind: Kind::Str,
        presence: Presence::Required,
    },
    Field {
        name: "conversation",
        kind: Kind::Str,
        presence: Presence::Optional,
    },
    Field {
        name: "limit",
        kind: Kind::Int,
        presence: Presence::Default("10"),
    },
];

pub fn recall() -> Tool {
    Tool {
        name: "recall_turn",
        description: quoted(TOOLS, "recall_turn_any").expect("tools.yaml carries recall_turn_any"),
        params: mapping(TOOLS, "recall_turn_any_params").expect("tools.yaml carries the params"),
        fields: &RECALL_FIELDS,
        read_only: true,
        destructive: false,
        call: recall_call,
    }
}

pub fn search() -> Tool {
    Tool {
        name: "search_history",
        description: quoted(TOOLS, "search_history_any")
            .expect("tools.yaml carries search_history_any"),
        params: mapping(TOOLS, "search_history_any_params").expect("tools.yaml carries the params"),
        fields: &SEARCH_FIELDS,
        read_only: true,
        destructive: false,
        call: search_call,
    }
}

fn mailbox(server: &Server) -> Mailbox<'_> {
    let memory = &server.memory;
    Mailbox::of_mounts(&memory.store, &memory.mounts, None).with_actor(memory.actor.clone())
}

fn recall_call(server: &Server, arguments: &Map<String, Value>) -> Outcome {
    let turn = integer(arguments, "turn").unwrap_or_default();
    let conversation = text(arguments, "conversation").unwrap_or_default();
    let label = format!("Conversation {}", repr(conversation));
    let call = text(arguments, "call");
    let found = recalling::recall(&server.memory.store, conversation, turn, &label, call);
    let text = match found {
        Ok(Ok(text)) => text,
        Ok(Err(corrective)) => {
            let outcome = Outcome::fail(corrective.error);
            return match corrective.hint {
                Some(hint) => outcome.hint(hint),
                None => outcome,
            };
        }
        Err(error) => return Outcome::failed("recall_turn", &error.text()),
    };
    match augment_recall(&mailbox(server), text, conversation, turn) {
        Ok(text) => Outcome::ok(text),
        Err(error) => Outcome::failed("recall_turn", &error.text()),
    }
}

fn search_call(server: &Server, arguments: &Map<String, Value>) -> Outcome {
    let query = text(arguments, "query").unwrap_or_default();
    let terms = match parse_query(query) {
        Ok(terms) => terms,
        Err(_) => {
            return Outcome::fail("Give at least one search term")
                .hint("A turn matches when it holds every term, case-insensitively.");
        }
    };
    let limit = integer(arguments, "limit").unwrap_or(10);
    if limit < 1 {
        return Outcome::fail(format!("limit must be at least 1; got {limit}"));
    }
    let shown = limit.min(MAX_HITS) as usize;
    let narrowed = text(arguments, "conversation").map(|id| vec![id.to_string()]);
    let ids = narrowed.as_deref();
    let turns = match server.memory.store.search_turns(&terms, ids, shown) {
        Ok(turns) => turns,
        Err(Error::Conversation(refused)) => {
            return Outcome::fail(format!("[{}] {}", refused.code, refused.message));
        }
        Err(error) => return Outcome::failed("search_history", &error.text()),
    };
    match augment_search(&mailbox(server), &turns, &terms, ids, shown) {
        Ok(Some(merged)) => return Outcome::ok(merged),
        Ok(None) => {}
        Err(error) => return Outcome::failed("search_history", &error.text()),
    }
    if turns.is_empty() {
        let listed: Vec<String> = terms.iter().map(|term| repr(term)).collect();
        return Outcome::ok(format!(
            "No turn matches every term: {}. Try fewer or different terms.",
            listed.join(", ")
        ));
    }
    let footer = render(
        &quoted(TOOLS, "search_history_footer").expect("tools.yaml carries the footer"),
        &[("shown", &shown.to_string())],
    );
    let mut lines: Vec<String> = turns.iter().map(|turn| hit_block(turn, &terms)).collect();
    lines.push(footer);
    Outcome::ok(lines.join("\n"))
}
