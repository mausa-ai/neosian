//! Bounded context injection (`messaging/delivery.py`, `history.py`,
//! DESIGN §34): the summary line of a message, the inbox frame a hook
//! delivers within its budget, and the annotation a search hit carries.
//! Rendering never marks anything read, including overflow or failure.

use super::mailbox::Mailbox;
use super::types::InboxMessage;
use crate::assets::{MESSAGES, block, render};
use crate::conversation::Error;
use crate::text::{head, len};

/// `MailboxConfig.context_chars`.
pub const CONTEXT_CHARS: usize = 2048;

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
    let items = mailbox.list()?;
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
