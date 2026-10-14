//! What a message reads as (`messaging/delivery.py`, `history.py`,
//! DESIGN §34): the summary line, and the annotation a search hit
//! carries. The inbox frame a hook delivers lands with the record verb.

use super::types::InboxMessage;

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
