//! Pure, fenced lifecycle transitions (`messaging/transitions.py`, DESIGN
//! §34): the next state of one message under one action by one session,
//! or the sentence that refuses it. The service supplies the version
//! check and the clock; a token is minted here.

use uuid::Uuid;

use super::types::{Action, CLAIM_SECONDS, Delivery, InboxMessage, RESERVATION_SECONDS};
use crate::store::time::Stamp;

/// What the acting session supplies beside the action.
pub struct Move<'a> {
    pub session: &'a str,
    pub occurrence: i64,
    pub token: Option<&'a str>,
    pub now: Stamp,
    pub outcome: Option<&'a str>,
    pub due_at: Option<Stamp>,
}

fn blank(text: Option<&str>) -> bool {
    text.is_none_or(|text| text.trim().is_empty())
}

fn token() -> Option<String> {
    Some(Uuid::new_v4().to_string())
}

/// The message after `action`, equal to itself when nothing changes.
pub fn transition(item: &InboxMessage, action: Action, m: &Move) -> Result<InboxMessage, String> {
    if m.occurrence != item.occurrence {
        return Err("stale occurrence; view the message again".to_string());
    }
    if item.status != "open" {
        let settled = action == Action::Complete
            && item.status == "done"
            && m.token == item.claim_token.as_deref()
            && Some(m.session) == item.owner.as_deref();
        if settled || (action == Action::Cancel && item.status == "cancelled") {
            return Ok(item.clone());
        }
        return Err(format!("message is {}", item.status));
    }
    let mut next = item.clone();
    if action == Action::Cancel {
        next.status = "cancelled".to_string();
        next.outcome = m.outcome.map(str::to_string);
        return Ok(next);
    }
    if !item.available(m.now) {
        return Err("message is not due yet".to_string());
    }
    if matches!(action, Action::Reserve | Action::Accepted | Action::Retry) {
        return wake(item, action, m);
    }
    if action == Action::Ack {
        if item.claimed(m.now) && item.owner.as_deref() != Some(m.session) {
            return Err("message is claimed by another session".to_string());
        }
        if item.read_at.is_some() && !item.unread(m.now) {
            return Ok(next);
        }
        next.read_by = Some(m.session.to_string());
        next.read_at = Some(m.now);
        next.owner = None;
        next.claim_token = None;
        next.claim_until = None;
        return Ok(next);
    }
    if !item.actionable {
        return Err("message is informational; acknowledge it instead".to_string());
    }
    if action == Action::Claim {
        if item.claimed(m.now) {
            if item.owner.as_deref() == Some(m.session) {
                return Ok(next);
            }
            return Err("message is claimed by another session".to_string());
        }
        next.owner = Some(m.session.to_string());
        next.claim_token = token();
        next.claim_until = Some(m.now.after(CLAIM_SECONDS));
        next.read_by = Some(m.session.to_string());
        next.read_at = Some(m.now);
        return Ok(next);
    }
    if !item.claimed(m.now)
        || item.owner.as_deref() != Some(m.session)
        || m.token != item.claim_token.as_deref()
    {
        return Err("a current claim token owned by this session is required".to_string());
    }
    match action {
        Action::Renew => next.claim_until = Some(m.now.after(CLAIM_SECONDS)),
        Action::Complete => {
            if blank(m.outcome) {
                return Err("completion requires an outcome".to_string());
            }
            next.status = "done".to_string();
            next.outcome = m.outcome.map(str::to_string);
        }
        Action::Snooze => {
            if m.due_at.is_none_or(|due| due.instant() <= m.now.instant()) {
                return Err("snooze requires a future due time".to_string());
            }
            if blank(m.outcome) {
                return Err("snooze requires the latest finding".to_string());
            }
            next.due_at = m.due_at.expect("checked above");
            next.outcome = m.outcome.map(str::to_string);
            next.occurrence = item.occurrence + 1;
        }
        _ => {} // release
    }
    if matches!(action, Action::Snooze | Action::Release) {
        next.owner = None;
        next.claim_token = None;
        next.claim_until = None;
        next.read_by = None;
        next.read_at = None;
        next.wake_owner = None;
        next.wake_token = None;
        next.wake_until = None;
        next.wake_accepted = false;
    }
    Ok(next)
}

fn wake(item: &InboxMessage, action: Action, m: &Move) -> Result<InboxMessage, String> {
    let mut next = item.clone();
    if action == Action::Reserve {
        if item.delivery != Delivery::Wake || !item.unread(m.now) || item.wake_accepted {
            return Err("no wake is pending".to_string());
        }
        if item.claimed(m.now) {
            return Err("work is already claimed".to_string());
        }
        if item
            .wake_until
            .is_some_and(|until| until.instant() > m.now.instant())
        {
            return Err("wake is reserved by another receiver".to_string());
        }
        next.wake_owner = Some(m.session.to_string());
        next.wake_token = token();
        next.wake_until = Some(m.now.after(RESERVATION_SECONDS));
        return Ok(next);
    }
    if item.wake_owner.as_deref() != Some(m.session)
        || item.wake_token.as_deref() != m.token
        || item
            .wake_until
            .is_none_or(|until| until.instant() <= m.now.instant())
    {
        return Err("a current wake reservation is required".to_string());
    }
    next.wake_accepted = action == Action::Accepted;
    next.wake_owner = None;
    next.wake_token = None;
    next.wake_until = None;
    Ok(next)
}

#[cfg(test)]
mod tests;
