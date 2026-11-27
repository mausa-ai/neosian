//! The notices of one session start (DESIGN §40.54 item 17.8; the T1e2b
//! slice and the owner rulings of the T1e2 plan). Before the context is
//! built the hook reads what was told, then the metadata, and keeps room
//! for what it would tell (`peek`), so the context below stays inside the
//! client's budget and the notices come first, where a client that cuts
//! the end keeps them whole. With the context built it takes
//! `notices.lock` without waiting (`claim`): when what was told is
//! unchanged it tells what it found; when another start told something
//! meanwhile it reads the metadata again under the lock and tells only
//! what still fits; when the lock is taken it tells nothing. What it tells
//! is kept only after the answer was written out and flushed (`answer`),
//! so a notice is never marked told before it was written, and a start
//! that fails to write shows it again later. A start whose own context
//! could not be built still writes its notices, alone, and says what failed
//! on stderr (`alone`). Nothing here opens a store or reaches a network;
//! a home with nothing to tell writes nothing and its context is unchanged.

use std::fs::File;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use serde_json::Value;

use super::destination::stamp;
use super::notice::{Notice, block, fit, fresh};
use super::slots::directory;
use super::snapshot::{Snapshot, take};
use super::start::handed;
use super::told::{self, Told};
use super::usage::trees;
use crate::conversation::ids::parse_conversation_id;
use crate::record::output::{Envelope, respond};
use crate::record::payload::{Payload, SESSION_START_EVENT};
use crate::record::settings::Settings;
use crate::store::grammar::Actor;

/// What one session start found to tell, before its context is built.
pub struct Startup {
    home: PathBuf,
    trees: Vec<PathBuf>,
    url: Option<String>,
    now: SystemTime,
    before: Option<Vec<u8>>,
    snapshot: Snapshot,
    notices: Vec<Notice>,
    room: usize,
}

/// Notices claimed and written into an answer, kept once it is out.
pub struct Delivery {
    _lock: File,
    landing: PathBuf,
    told: Told,
}

impl Delivery {
    /// Keeps what was told; a failure leaves the notices to be shown again.
    pub fn commit(self) -> io::Result<()> {
        told::write(&self.landing, &self.told)
    }
}

impl Startup {
    /// What a start of the home at `home` would tell from `trees` at
    /// `now`, or `None` when nothing is new.
    pub fn peek(
        home: &Path,
        trees: Vec<PathBuf>,
        url: Option<String>,
        now: SystemTime,
    ) -> Option<Self> {
        let (before, told) = told::read(&directory(home));
        let snapshot = take(home, &trees, url.as_deref(), None);
        let notices = fit(fresh(&snapshot, &told, stamp(now)), usize::MAX);
        if notices.is_empty() {
            return None;
        }
        let room = crate::text::len(&block(&notices)) + 2;
        Some(Self {
            home: home.to_path_buf(),
            trees,
            url,
            now,
            before,
            snapshot,
            notices,
            room,
        })
    }

    /// The characters the notices take ahead of the context.
    pub fn reserve(&self) -> usize {
        self.room
    }

    /// Claims the notices and puts them at the head of `context`, or as
    /// all of it when there is none; `None` when another start holds the
    /// lock or has told them meanwhile.
    pub fn claim(self, context: &mut Option<String>) -> Option<Delivery> {
        let landing = directory(&self.home);
        let lock = told::try_lock(&landing)?;
        let (current, told) = told::read(&landing);
        let (snapshot, notices) = if current == self.before {
            (self.snapshot, self.notices)
        } else {
            let snapshot = take(&self.home, &self.trees, self.url.as_deref(), None);
            let notices = fresh(&snapshot, &told, stamp(self.now));
            (snapshot, fit(notices, self.room))
        };
        if notices.is_empty() {
            return None;
        }
        let text = block(&notices);
        *context = Some(match context.take() {
            Some(rest) => format!("{text}\n\n{rest}"),
            None => text,
        });
        let claims: Vec<_> = notices.iter().map(|notice| &notice.claim).collect();
        let told = told.after(&claims, &snapshot);
        Some(Delivery {
            _lock: lock,
            landing,
            told,
        })
    }
}

fn starts(payload: &Payload) -> bool {
    payload.get("hook_event_name").and_then(Value::as_str) == Some(SESSION_START_EVENT)
}

/// A direct hook's start: the home's trees and its own, and its URL when
/// the descriptor cannot carry it. `None` for any other event.
pub fn direct(payload: &Payload, settings: &Settings) -> Option<Startup> {
    if !starts(payload) {
        return None;
    }
    let home = crate::home::home().ok()?;
    let url = handed(&settings.store).map(str::to_string);
    Startup::peek(&home, trees(&home, &settings.spool), url, SystemTime::now())
}

/// A profile hook's start in the home at `home`. `None` for any other
/// event, Pi's `MailboxContext` before each prompt included.
pub fn profile(payload: &Payload, home: &Path) -> Option<Startup> {
    if !starts(payload) {
        return None;
    }
    let tree = home.join("profile-spool");
    Startup::peek(home, trees(home, &tree), None, SystemTime::now())
}

/// Writes `envelope` as the client reads it, with the notices of
/// `startup` claimed at the head of its context, and keeps them told once
/// the answer is flushed.
pub fn answer(
    startup: Option<Startup>,
    envelope: &mut Envelope,
    agent: &str,
    json: bool,
    out: &mut impl Write,
) -> io::Result<()> {
    let delivery = startup.and_then(|startup| startup.claim(&mut envelope.context));
    respond(envelope, agent, json, out)?;
    if let Some(delivery) = delivery {
        out.flush()?;
        let _ = delivery.commit();
    }
    Ok(())
}

/// When a session start's context failed with `error`: its notices alone,
/// exit 0, with the failure on stderr as `failed` writes it. `ids` are
/// the envelope's session id and the native one its actor names. `None`
/// when there is nothing to tell or the native id is not one, and the
/// caller fails as before.
pub fn alone(
    startup: Option<Startup>,
    agent: &str,
    ids: (&str, &str),
    json: bool,
    error: (&str, Option<&str>),
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<Option<u8>> {
    let Some(startup) = startup else {
        return Ok(None);
    };
    let (session_id, native) = ids;
    let actor = parse_conversation_id(native)
        .ok()
        .and_then(|_| Actor::parse(&format!("{agent}:{native}")).ok());
    let Some(actor) = actor else {
        return Ok(None);
    };
    let mut envelope = Envelope {
        event: SESSION_START_EVENT.to_string(),
        session_id: session_id.to_string(),
        actor,
        disposition: "context",
        conversation_id: None,
        turn: None,
        document: None,
        client: None,
        context: None,
        messages: None,
    };
    let Some(delivery) = startup.claim(&mut envelope.context) else {
        return Ok(None);
    };
    writeln!(err, "error: {}", error.0)?;
    if let Some(hint) = error.1 {
        writeln!(err, "hint: {hint}")?;
    }
    respond(&envelope, agent, json, out)?;
    out.flush()?;
    let _ = delivery.commit();
    Ok(Some(0))
}
