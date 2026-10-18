//! The session table (`streamable_http_manager.py` in the stateful mode
//! the Python door runs, DESIGN §39.11): a session is minted for the
//! request that opens it, its id `uuid4().hex`, holds the dispatcher's
//! gate, is held open by a request being served or a standing GET stream,
//! and is forgotten by DELETE, after thirty idle minutes, or at shutdown.
//! The SDK reaps an idle session on a timer; here it is reaped when next
//! looked up, which a client cannot tell apart.

use std::collections::HashMap;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use tokio::sync::watch;
use uuid::Uuid;

use crate::mcp::rpc::Gate;

/// `DEFAULT_SESSION_IDLE_TIMEOUT`.
pub const IDLE: Duration = Duration::from_secs(30 * 60);
/// `DEFAULT_MAX_SESSIONS`.
pub const MAX_SESSIONS: usize = 10_000;

pub struct Session {
    pub gate: Gate,
    in_flight: usize,
    idle_since: Instant,
    stream_open: bool,
    /// Flipped once, when the session is terminated: ends its GET stream.
    closed: watch::Sender<bool>,
}

impl Session {
    fn expired(&self, now: Instant) -> bool {
        self.in_flight == 0 && !self.stream_open && now.duration_since(self.idle_since) >= IDLE
    }
}

#[derive(Default)]
pub struct Table(HashMap<String, Session>);

pub type Sessions = Arc<Mutex<Table>>;

/// The table is full: 503 `Too many open sessions`.
#[derive(Debug)]
pub struct Full;

/// Why a stream could not open: no live session, or one is open already.
#[derive(Debug)]
pub enum Streamless {
    Absent,
    Conflict,
}

pub fn locked(sessions: &Sessions) -> MutexGuard<'_, Table> {
    sessions.lock().unwrap_or_else(PoisonError::into_inner)
}

impl Table {
    /// A new session with its minted id, the expired ones swept first.
    pub fn admit(&mut self, now: Instant) -> Result<String, Full> {
        self.0.retain(|_, session| !session.expired(now));
        if self.0.len() >= MAX_SESSIONS {
            return Err(Full);
        }
        let id = Uuid::new_v4().simple().to_string();
        let (closed, _) = watch::channel(false);
        let session = Session {
            gate: Gate::default(),
            in_flight: 0,
            idle_since: now,
            stream_open: false,
            closed,
        };
        self.0.insert(id.clone(), session);
        Ok(id)
    }

    /// The live session named; an expired one is forgotten on the way.
    pub fn find(&mut self, id: &str, now: Instant) -> Option<&mut Session> {
        if self.0.get(id).is_some_and(|session| session.expired(now)) {
            self.0.remove(id);
        }
        self.0.get_mut(id)
    }

    /// DELETE: the session forgotten, its stream ended.
    pub fn terminate(&mut self, id: &str) {
        if let Some(session) = self.0.remove(id) {
            let _ = session.closed.send(true);
        }
    }
}

/// A request in flight or a GET stream open: the session is held until
/// this drops, and its idle clock restarts then.
pub struct Hold {
    sessions: Sessions,
    id: String,
    stream: bool,
}

impl Drop for Hold {
    fn drop(&mut self) {
        if let Some(session) = locked(&self.sessions).0.get_mut(&self.id) {
            if self.stream {
                session.stream_open = false;
            } else {
                session.in_flight -= 1;
            }
            session.idle_since = Instant::now();
        }
    }
}

/// The session held for one request; None when `id` names no live one.
pub fn hold(sessions: &Sessions, id: &str, now: Instant) -> Option<Hold> {
    locked(sessions).find(id, now)?.in_flight += 1;
    Some(Hold {
        sessions: sessions.clone(),
        id: id.to_string(),
        stream: false,
    })
}

/// The session's one standalone stream opened: the receiver ends it.
pub fn open_stream(
    sessions: &Sessions,
    id: &str,
    now: Instant,
) -> Result<(Hold, watch::Receiver<bool>), Streamless> {
    let mut table = locked(sessions);
    let session = table.find(id, now).ok_or(Streamless::Absent)?;
    if session.stream_open {
        return Err(Streamless::Conflict);
    }
    session.stream_open = true;
    let closed = session.closed.subscribe();
    let hold = Hold {
        sessions: sessions.clone(),
        id: id.to_string(),
        stream: true,
    };
    Ok((hold, closed))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sessions() -> Sessions {
        Arc::new(Mutex::new(Table::default()))
    }

    #[test]
    fn a_session_is_minted_held_reaped_and_terminated() {
        let sessions = sessions();
        let now = Instant::now();
        let id = locked(&sessions).admit(now).unwrap();
        assert_eq!(id.len(), 32);
        assert!(id.bytes().all(|b| b.is_ascii_hexdigit()));
        assert!(locked(&sessions).find("nope", now).is_none());

        let later = now + IDLE;
        let held = hold(&sessions, &id, now).unwrap();
        assert!(
            locked(&sessions).find(&id, later).is_some(),
            "held: never reaped"
        );
        drop(held);
        let (stream, closed) = open_stream(&sessions, &id, now).unwrap();
        assert!(matches!(
            open_stream(&sessions, &id, now),
            Err(Streamless::Conflict)
        ));
        assert!(
            locked(&sessions).find(&id, now + IDLE * 2).is_some(),
            "streaming: never reaped"
        );
        drop(stream);
        assert!(!*closed.borrow());
        locked(&sessions).terminate(&id);
        assert!(*closed.borrow(), "terminated: the stream ends");
        assert!(hold(&sessions, &id, now).is_none());

        let id = locked(&sessions).admit(now).unwrap();
        assert!(
            locked(&sessions).find(&id, now + IDLE).is_none(),
            "idle: reaped"
        );
        assert!(matches!(
            open_stream(&sessions, &id, now),
            Err(Streamless::Absent)
        ));
    }

    #[test]
    fn the_table_refuses_past_its_ceiling() {
        let sessions = sessions();
        let now = Instant::now();
        for _ in 0..MAX_SESSIONS {
            assert!(locked(&sessions).admit(now).is_ok());
        }
        assert!(locked(&sessions).admit(now).is_err());
        assert!(locked(&sessions).admit(now + IDLE).is_ok(), "swept first");
    }
}
