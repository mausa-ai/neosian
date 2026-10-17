//! Bounded context injection (`messaging/delivery.py`, `history.py`,
//! DESIGN §34): the summary line of a message, the inbox frame a hook
//! delivers within its budget, and the annotation a search hit carries.
//! Rendering never marks anything read, including overflow or failure.

use super::mailbox::{LIST_LIMIT, ListStatus, Mailbox};
use super::types::{CONTEXT_CHARS, InboxMessage};
use crate::assets::{MESSAGES, block, render};
use crate::conversation::Error;
use crate::text::{head, len};

pub fn summary(item: &InboxMessage) -> String {
    format!(
        "[{} occurrence={} scope={} from={} at={} status={} actionable={}]\n{}",
        item.id,
        item.occurrence,
        item.scope,
        item.actor,
        item.created_at.utc().iso(),
        item.status,
        if item.actionable { "True" } else { "False" },
        item.body
    )
}

/// The summary, and the latest outcome when one was recorded.
pub fn annotation(item: &InboxMessage) -> String {
    match item
        .outcome
        .as_deref()
        .filter(|outcome| !outcome.is_empty())
    {
        Some(outcome) => format!("{}\nLatest outcome: {outcome}", summary(item)),
        None => summary(item),
    }
}

/// The inbox frame within `CONTEXT_CHARS`: every unread summary that
/// fits, the last one cut to the room left, nothing when there is none.
pub fn context(mailbox: &Mailbox) -> Result<String, Error> {
    let items = mailbox.list(None, ListStatus::Unread, LIST_LIMIT, true, false)?;
    if items.is_empty() {
        return Ok(String::new());
    }
    let template = block(MESSAGES, "context").expect("messages.yaml carries the context frame");
    let session = mailbox.session().unwrap_or("unbound");
    let frame = render(&template, &[("session", session), ("items", "")]);
    let room = CONTEXT_CHARS.saturating_sub(len(&frame));
    let mut lines: Vec<String> = Vec::new();
    let mut used = 0;
    for receipt in &items {
        let mut line = summary(&receipt.message);
        if used + len(&line) > room {
            line = head(&line, room.saturating_sub(used).saturating_sub(1)).to_string();
            if !line.is_empty() {
                lines.push(line);
            }
            break;
        }
        used += len(&line) + 1;
        lines.push(line);
    }
    let items = lines.join("\n");
    let text = render(&template, &[("session", session), ("items", &items)]);
    Ok(head(&text, CONTEXT_CHARS).to_string())
}

/// The one message a wake delivers, framed, the body cut to what the
/// budget leaves after the frame.
pub fn wake_text(item: &InboxMessage) -> String {
    let template = block(MESSAGES, "wake").expect("messages.yaml carries the wake frame");
    let occurrence = item.occurrence.to_string();
    let frame = render(
        &template,
        &[
            ("id", item.id.as_str()),
            ("occurrence", occurrence.as_str()),
            ("scope", item.scope.as_str()),
            ("actor", item.actor.as_str()),
        ],
    );
    let room = (CONTEXT_CHARS + len("{{body}}")).saturating_sub(len(&frame));
    let text = render(&frame, &[("body", head(&item.body, room))]);
    head(&text, CONTEXT_CHARS).to_string()
}
