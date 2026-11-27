//! Which hook starts a lander (DESIGN §40.54 item 17.3; rulings D2, D4,
//! D13 and D15 and owner question O3 of the T1e1 plan). Stop starts one
//! for the turn it closed when that turn does not land inline;
//! UserPromptSubmit when its own conversation has a queued turn, which one
//! directory listing tells; SessionStart when a queued turn of its spool
//! tree waits for a destination its environment can serve, which reading
//! the states tells; PostToolUse never, its budget being 10 ms. None
//! starts one for a destination that waits or is blocked, as its record
//! says (`destination.rs`), unless the block's six hours have passed: that
//! hook event starts the one attempt. Stop and UserPromptSubmit read their
//! conversation's queued states for it, and only when a turn is queued.
//! None starts one when both slots are taken either. The slots are probed
//! last and released before the spawn. A spawn that fails leaves the turns
//! queued for the next hook event, and the hook answers as it would have.

use std::io;
use std::path::Path;
use std::time::SystemTime;

use super::destination::{self, Gate, key, stamp};
use super::files::{Part, entries};
use super::queue::Queue;
use super::serving::serving;
use super::slots;
use super::spawn::{Lander, spawn};
use super::state::{Destination, State, public_url};
use super::verb::conversations;
use crate::record::payload::{PROMPT_EVENT, RESPONSE_EVENT, SESSION_START_EVENT, STOP_EVENT};
use crate::record::settings::Settings;
use crate::remote::selection::Selection;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Event {
    Stop,
    Prompt,
    SessionStart,
    Tool,
}

impl Event {
    /// A hook event by its name; an event that closes no turn and is not
    /// named here starts nothing, as PostToolUse.
    pub fn of(name: &str) -> Self {
        match name {
            STOP_EVENT | RESPONSE_EVENT => Self::Stop,
            PROMPT_EVENT => Self::Prompt,
            SESSION_START_EVENT => Self::SessionStart,
            _ => Self::Tool,
        }
    }
}

/// Where a hook records: its home, its spool tree, its conversation, and
/// the URL a lander must be handed.
pub struct Hook<'a> {
    pub home: &'a Path,
    pub tree: &'a Path,
    pub id: &'a str,
    pub url: Option<&'a str>,
}

/// What the hook found before it probes the slots.
#[derive(Clone, Copy, Debug, Default)]
pub struct Found {
    /// Stop: the turn it closed waits; UserPromptSubmit: its conversation
    /// has a queued turn; SessionStart: a queued turn waits for a
    /// destination this environment serves.
    pub queued: bool,
    /// The destination is blocked until it resumes (17.5).
    pub blocked: bool,
    /// The destination waits until a time not yet reached (17.4).
    pub waiting: bool,
}

impl Found {
    /// A queued turn, or none, for a destination neither blocked nor
    /// waiting.
    pub fn queued(queued: bool) -> Self {
        Self {
            queued,
            ..Self::default()
        }
    }
}

/// Whether `event` starts a lander for what it `found`; `slot` probes the
/// slots, last, since it is the costliest step.
pub fn starts(
    event: Event,
    found: Found,
    slot: impl FnOnce() -> io::Result<bool>,
) -> io::Result<bool> {
    if event == Event::Tool || !found.queued || found.blocked || found.waiting {
        return Ok(false);
    }
    slot()
}

/// Starts a lander when the rules say so; whether one was spawned.
pub fn start(event: Event, found: Found, lander: &Lander) -> bool {
    matches!(starts(event, found, || slots::free(lander.home)), Ok(true)) && spawn(lander).is_ok()
}

/// After hook event `name`, where `closed` says a Stop left the turn it
/// closed queued: what the rules ask the hook to look at, then the start.
/// A lander serves the hook's tree when it is not one of the home's own.
pub fn after(name: &str, closed: bool, hook: &Hook) -> bool {
    let event = Event::of(name);
    let found = match event {
        Event::Stop if closed => queued(hook.tree, hook.home, hook.id),
        Event::Prompt => queued(hook.tree, hook.home, hook.id),
        Event::SessionStart => Found::queued(waits_here(hook.tree, hook.home, hook.url)),
        Event::Stop | Event::Tool => Found::default(),
    };
    let own = [hook.home.join("spool"), hook.home.join("profile-spool")];
    let lander = Lander {
        home: hook.home,
        spool: (!own.iter().any(|tree| tree == hook.tree)).then_some(hook.tree),
        url: hook.url,
    };
    start(event, found, &lander)
}

/// What conversation `id`'s queued turns under `tree` find: whether one is
/// queued, and whether every destination they name waits or is blocked, as
/// the records under `home` say now. A turn whose state cannot be read or
/// names no destination is not landable: a lander that runs for another
/// turn sets it aside (ruling G7).
pub fn queued(tree: &Path, home: &Path, id: &str) -> Found {
    let directory = Queue::new(tree).pending_dir(id);
    let Ok(listed) = entries(&directory) else {
        return Found::default();
    };
    let (landing, now) = (slots::directory(home), stamp(SystemTime::now()));
    let mut found = Found::default();
    let turns = listed
        .iter()
        .filter(|(_, parts)| parts.iter().any(|(part, _)| *part == Part::Span));
    for (n, _) in turns {
        let state = State::read(&Queue::part(&directory, *n, "state"));
        let Some(destination) = state.ok().and_then(|state| state.destination) else {
            continue;
        };
        found.queued = true;
        let record = destination::read(&landing, &key(&destination));
        match record
            .as_ref()
            .map(|record| (record.gate(now), record.block.is_some()))
        {
            Some((Gate::Closed, true)) => found.blocked = true,
            Some((Gate::Closed, false)) => found.waiting = true,
            _ => return Found::queued(true),
        }
    }
    found
}

/// A direct hook's start (`record/cli.rs`): its spool tree, and its URL
/// when the descriptor cannot carry it.
pub fn direct(settings: &Settings, id: &str, name: &str, closed: bool) -> bool {
    let Ok(home) = crate::home::home() else {
        return false;
    };
    let url = handed(&settings.store);
    let tree = &settings.spool;
    after(
        name,
        closed,
        &Hook {
            home: &home,
            tree,
            id,
            url,
        },
    )
}

/// A profile hook's start (`record/profile.rs`): the home's
/// `profile-spool/`, which is `tree`.
pub fn profile(tree: &Path, id: &str, name: &str, closed: bool) -> bool {
    let Some(home) = tree.parent() else {
        return false;
    };
    after(
        name,
        closed,
        &Hook {
            home,
            tree,
            id,
            url: None,
        },
    )
}

/// The URL a lander is handed for a direct destination: a queued turn's
/// descriptor keeps no userinfo and no query (ruling E3), so a URL that
/// carried either is passed on whole.
pub fn handed(selection: &Selection) -> Option<&str> {
    match selection {
        Selection::Remote { url, .. } if public_url(url) != url.trim_end_matches('/') => Some(url),
        _ => None,
    }
}

/// Whether a queued turn under `tree` waits for a destination this
/// process's environment can serve (D2) and that neither waits nor is
/// blocked short of its attempt. States are read until one is found; a
/// destination found unservable or closed is not checked again.
pub fn waits_here(tree: &Path, home: &Path, url: Option<&str>) -> bool {
    let queue = Queue::new(tree);
    let now = stamp(SystemTime::now());
    let mut unservable: Vec<Destination> = Vec::new();
    let Ok(ids) = conversations(tree) else {
        return false;
    };
    for id in ids {
        let directory = queue.pending_dir(&id);
        let Ok(listed) = entries(&directory) else {
            continue;
        };
        for n in listed.keys() {
            let Ok(state) = State::read(&Queue::part(&directory, *n, "state")) else {
                continue;
            };
            let Some(destination) = &state.destination else {
                continue;
            };
            if unservable.contains(destination) {
                continue;
            }
            let open = destination::gate(&slots::directory(home), destination, now) != Gate::Closed;
            if open && serving(destination, &state, home, url).is_some() {
                return true;
            }
            unservable.push(destination.clone());
        }
    }
    false
}
