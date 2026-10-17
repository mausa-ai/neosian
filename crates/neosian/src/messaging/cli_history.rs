//! Store-wide operator search joins messages where scopes are enumerable
//! (`messaging/cli_history.py`): the turn hits keep their fields and
//! order, a message about a turn annotates it, and a message whose body
//! or outcomes hold every term is a hit of its own, typed.

use serde_json::{Value, json};

use super::delivery::annotation;
use super::mailbox::Mailbox;
use crate::conversation::hits::{hit_json, snippet};
use crate::conversation::search::match_terms;
use crate::conversation::{Error, Turn};
use crate::store::FileStore;

/// A search answer: a turn with the annotations about it, or a message.
pub enum Hit<'a> {
    Turn {
        turn: &'a Turn,
        annotations: Vec<String>,
    },
    Message {
        json: Value,
        annotation: String,
        snippet: String,
    },
}

impl Hit<'_> {
    pub fn json(&self, terms: &[String]) -> Value {
        match self {
            Self::Turn { turn, annotations } => {
                let mut hit = hit_json(turn, terms);
                if !annotations.is_empty() {
                    hit["annotations"] = json!(annotations);
                }
                hit
            }
            Self::Message { json, .. } => json.clone(),
        }
    }
}

/// The turn hits, annotated and joined by the message hits, newest first,
/// the newest `limit`.
pub fn search_hits<'a>(
    store: &FileStore,
    turns: &'a [Turn],
    terms: &[String],
    ids: Option<&[String]>,
    limit: usize,
) -> Result<Vec<Hit<'a>>, Error> {
    let mut rows: Vec<(i64, String, Hit<'a>)> = turns
        .iter()
        .map(|turn| {
            let key = format!("turn:{}:{:020}", turn.conversation_id, turn.turn);
            let hit = Hit::Turn {
                turn,
                annotations: Vec::new(),
            };
            (turn.created_at.instant(), key, hit)
        })
        .collect();
    let scopes = store.scopes()?;
    if !scopes.is_empty() {
        let mailbox = Mailbox::over_scopes(store, scopes);
        for receipt in mailbox.all(None)? {
            let item = &receipt.message;
            let about = item.about.as_deref();
            if ids.is_some_and(|ids| !about.is_some_and(|about| ids.iter().any(|id| id == about))) {
                continue;
            }
            let note = annotation(item);
            for (_, _, hit) in &mut rows {
                if let Hit::Turn { turn, annotations } = hit
                    && about == Some(turn.conversation_id.as_str())
                    && item
                        .about_turn
                        .is_none_or(|number| number == turn.turn as i64)
                {
                    annotations.push(note.clone());
                }
            }
            let mut text = item.body.clone();
            for version in mailbox.history(&item.scope, &item.id)? {
                text.push('\n');
                text.push_str(version.outcome.as_deref().unwrap_or(""));
            }
            if match_terms(&text, terms) {
                let clipped = snippet(&text, terms);
                let json = json!({
                    "type": "message",
                    "message_id": item.id,
                    "scope": item.scope,
                    "conversation_id": item.about,
                    "turn": item.about_turn,
                    "created_at": item.created_at.utc().z(),
                    "actor": item.actor,
                    "status": item.status,
                    "snippet": clipped,
                    "annotation": note,
                });
                let hit = Hit::Message {
                    json,
                    annotation: note,
                    snippet: clipped,
                };
                rows.push((
                    item.created_at.instant(),
                    format!("message:{}", item.id),
                    hit,
                ));
            }
        }
    }
    rows.sort_by(|a, b| (b.0, &b.1).cmp(&(a.0, &a.1)));
    rows.truncate(limit);
    Ok(rows.into_iter().map(|(_, _, hit)| hit).collect())
}
