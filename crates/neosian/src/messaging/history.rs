//! Annotations beside history (`messaging/history.py`, DESIGN §34): the
//! messages about a turn ride under a recall, a search merges the
//! messages whose body or outcomes hold every term, and a continuation
//! delivers the inbox the continued session's lineage may read. The
//! original turns are never edited.

use super::delivery::{annotation, context};
use super::mailbox::Mailbox;
use super::types::{CONTEXT_CHARS, InboxMessage};
use crate::assets::{MESSAGES, block, render};
use crate::conversation::Error;
use crate::conversation::Turn;
use crate::conversation::hits::{hit_line, snippet};
use crate::conversation::search::match_terms;
use crate::store::grammar::Actor;
use crate::text::head;

/// The annotations frame, the lines cut to the context budget; nothing
/// for no message.
pub fn annotation_block(items: &[&InboxMessage]) -> String {
    if items.is_empty() {
        return String::new();
    }
    let lines: Vec<String> = items.iter().map(|item| annotation(item)).collect();
    let template =
        block(MESSAGES, "annotations").expect("messages.yaml carries the annotations frame");
    render(
        &template,
        &[("items", head(&lines.join("\n\n"), CONTEXT_CHARS))],
    )
}

/// The messages about `target`, at its turn `turn` or about it whole.
fn about<'a>(
    receipts: &'a [super::mailbox::Receipt],
    target: &str,
    turn: Option<i64>,
) -> Vec<&'a InboxMessage> {
    receipts
        .iter()
        .map(|receipt| &receipt.message)
        .filter(|item| {
            item.about.as_deref() == Some(target)
                && (item.about_turn.is_none() || item.about_turn == turn)
        })
        .collect()
}

/// A recall's text with the annotations about that turn under it.
pub fn augment_recall(
    mailbox: &Mailbox,
    text: String,
    target: &str,
    turn: i64,
) -> Result<String, Error> {
    let receipts = mailbox.all(None)?;
    if receipts.is_empty() {
        return Ok(text);
    }
    let extra = annotation_block(&about(&receipts, target, Some(turn)));
    Ok(if extra.is_empty() {
        text
    } else {
        format!("{text}\n\n{extra}")
    })
}

/// The search answer with the mailbox merged: each hit with the
/// annotations about it, a matching message as a hit of its own, newest
/// first, the newest `limit`; None when the mailbox holds nothing, the
/// plain answer standing.
pub fn augment_search(
    mailbox: &Mailbox,
    turns: &[Turn],
    terms: &[String],
    ids: Option<&[String]>,
    limit: usize,
) -> Result<Option<String>, Error> {
    let receipts = mailbox.all(None)?;
    if receipts.is_empty() {
        return Ok(None);
    }
    let items: Vec<&InboxMessage> = receipts
        .iter()
        .map(|receipt| &receipt.message)
        .filter(|item| {
            ids.is_none_or(|ids| item.about.as_ref().is_some_and(|about| ids.contains(about)))
        })
        .collect();
    let mut rows: Vec<(i64, String, String)> = Vec::new();
    for turn in turns {
        let attached: Vec<&InboxMessage> = items
            .iter()
            .copied()
            .filter(|item| {
                item.about.as_deref() == Some(turn.conversation_id.as_str())
                    && item
                        .about_turn
                        .is_none_or(|number| number == turn.turn as i64)
            })
            .collect();
        let mut text = hit_line(turn, terms);
        if !attached.is_empty() {
            text.push('\n');
            text.push_str(&annotation_block(&attached));
        }
        rows.push((
            turn.created_at.instant(),
            format!("turn:{}:{:020}", turn.conversation_id, turn.turn),
            text,
        ));
    }
    for item in &items {
        let mut searchable = item.body.clone();
        for version in mailbox.history(&item.scope, &item.id)? {
            searchable.push('\n');
            searchable.push_str(version.outcome.as_deref().unwrap_or(""));
        }
        let searchable = searchable.to_lowercase();
        if match_terms(&searchable, terms) {
            let mut text = format!("{}\n{}", annotation(item), snippet(&searchable, terms));
            if let Some(about) = &item.about {
                let turn = item
                    .about_turn
                    .map_or("all".to_string(), |number| number.to_string());
                text.push_str(&format!(
                    "\nAnnotation of conversation {about}, turn {turn}"
                ));
            }
            rows.push((
                item.created_at.instant(),
                format!("message:{}", item.id),
                text,
            ));
        }
    }
    if rows.is_empty() {
        return Ok(None);
    }
    rows.sort_by(|a, b| (b.0, &b.1, &b.2).cmp(&(a.0, &a.1, &a.2)));
    rows.truncate(limit);
    let texts: Vec<String> = rows.into_iter().map(|(_, _, text)| text).collect();
    Ok(Some(texts.join("\n\n")))
}

/// What a continuation delivers from the mailbox: the lineage noted for
/// the calling session, then the continued session's pending inbox and
/// the annotations about it.
pub fn continuation_context(
    mailbox: &Mailbox,
    scope: &str,
    target: &str,
    actor: &Actor,
) -> Result<String, Error> {
    mailbox.note_continuation(scope, target)?;
    let reader = mailbox.as_session(Some(target)).with_actor(actor.clone());
    let pending = context(&reader)?;
    let receipts = reader.all(None)?;
    let notes = annotation_block(&about(&receipts, target, None));
    let parts: Vec<&str> = [pending.as_str(), notes.as_str()]
        .into_iter()
        .filter(|part| !part.is_empty())
        .collect();
    Ok(parts.join("\n\n"))
}
