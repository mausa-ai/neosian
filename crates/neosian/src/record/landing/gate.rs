//! A lander's landing of one conversation to one destination as the
//! destination's record allows (DESIGN §40.54 items 17.4 and 17.5; owner
//! decision O10). A destination that waits, or is blocked and not yet due,
//! is not asked. One that is blocked and whose six hours have passed gets
//! one attempt: the lander claims it (its next attempt moves six hours on,
//! so a second lander leaves it), and the attempt's first part decides
//! (`Trial`). Taken, the block ends before the run goes on, and the queue
//! lands as usual. Answered in any other way, the turn stays queued as it
//! was, nothing more is sent to the destination in that run, and its six
//! hours start over. An attempt that sent no part (the conversation was
//! busy, or nothing in it was for the destination) is given back. After
//! the run, an answer about the destination is written to its record,
//! timed from when it arrived, which ended the run (ruling G5), and with
//! what it said cleaned (`kept.rs`, ruling G3); a landed turn ends its
//! faults and its wait. Every time is read on the lander's clock, its cap's.

use std::cell::Cell;
use std::io;
use std::path::Path;

use super::answer::{Answer, said};
use super::destination::{Gate, claim, key, landed, lift, note, read, stamp, unclaim};
use super::kept::{self, credentials};
use super::queue::Queue;
use super::rules::{Action, Block, Stop};
use super::slots::directory;
use super::state::public_url;
use super::turn::{Here, Run, land_queue};
use crate::conversation::Error;
use crate::profile::files::Endpoint;
use crate::remote::selection::Selection;

/// The one attempt at a blocked destination whose six hours have passed.
pub struct Trial<'a> {
    landing: &'a Path,
    key: &'a str,
    block: Block,
    answered: Cell<bool>,
}

impl Trial<'_> {
    /// Whether no part of the attempt has been answered yet.
    pub fn pending(&self) -> bool {
        !self.answered.get()
    }

    /// The part was taken: the destination is unblocked now, and the run
    /// goes on.
    pub fn taken(&self) -> io::Result<()> {
        self.answered.set(true);
        lift(self.landing, self.key)
    }

    /// The part was answered with `action`: the stop that ends the run for
    /// the destination. A wait or a block is the answer's own; any other
    /// answer keeps the block it had, with the answer's words.
    pub fn refused(&self, action: Action, answer: &Answer) -> Stop {
        self.answered.set(true);
        match action {
            Action::Keep(stop @ (Stop::Block { .. } | Stop::Wait { .. })) => stop,
            Action::Keep(Stop::Retry { seconds, message }) => Stop::Wait {
                seconds,
                failing: false,
                message,
            },
            _ => {
                let (status, code) = match answer {
                    Answer::Refused(refusal) => (Some(refusal.status), refusal.code.clone()),
                    _ => (None, None),
                };
                Stop::Block {
                    block: self.block,
                    message: said(answer),
                    status,
                    code,
                }
            }
        }
    }
}

/// What a gated landing came to.
pub enum Gated {
    /// The destination waits or is blocked: nothing was asked.
    Closed,
    /// Another process holds the conversation's lock.
    Busy,
    Ran(Box<Run>),
}

fn named(reason: &str) -> Block {
    match reason {
        "storage" => Block::Storage,
        "credential" => Block::Credential,
        _ => Block::Forbidden,
    }
}

/// Lands conversation `id` of `queue` to the destination `here` serves, as
/// its record under `home` allows now on the lander's clock.
pub fn land(queue: &Queue, id: &str, here: &Here, home: &Path) -> Result<Gated, Error> {
    let landing = directory(home);
    let key = key(&here.destination);
    let now = stamp(here.cap.now());
    let record = read(&landing, &key);
    let claimed = match record.as_ref().map(|record| record.gate(now)) {
        Some(Gate::Closed) => return Ok(Gated::Closed),
        Some(Gate::Due) => match claim(&landing, &key, now)? {
            Some(block) => Some(block),
            None => return Ok(Gated::Closed),
        },
        _ => None,
    };
    let trial = claimed.as_ref().map(|block| Trial {
        landing: &landing,
        key: &key,
        block: named(&block.reason),
        answered: Cell::new(false),
    });
    let mut tried = here.clone();
    tried.trial = trial.as_ref();
    let untried = || match (&claimed, &trial) {
        (Some(was), Some(trial)) if trial.pending() => unclaim(&landing, &key, was),
        _ => Ok(()),
    };
    let Some(mut run) = land_queue(queue, id, &tried)? else {
        untried()?;
        return Ok(Gated::Busy);
    };
    let said = credentials(here.selection);
    run.stopped = run.stopped.map(|stop| kept::stop(stop, &said));
    match &run.stopped {
        Some(stop) if stop.destination() => {
            let arrived = stamp(here.cap.now());
            note(&landing, &key, &host(here.selection), stop, arrived)?;
        }
        _ => {
            untried()?;
            let stored = !run.landed.is_empty() || run.undocumented.is_some();
            if record.is_some() && stored {
                landed(&landing, &key)?;
            }
        }
    }
    Ok(Gated::Ran(Box::new(run)))
}

/// The host a person knows the destination by: a URL's host and port, a
/// local root's path, a DSN's host, never a credential.
pub fn host(selection: &Selection) -> String {
    let authority = |url: &str| {
        let shown = public_url(url);
        let rest = shown
            .split_once("://")
            .map_or(shown.as_str(), |(_, rest)| rest);
        rest.split('/').next().unwrap_or_default().to_string()
    };
    let postgres = |schema: &str| format!("PostgreSQL (schema {schema})");
    match selection {
        Selection::File(root) => root.display().to_string(),
        Selection::Postgres { dsn, schema } if dsn.contains("://") => {
            format!("{} (schema {schema})", authority(dsn))
        }
        Selection::Postgres { schema, .. } => postgres(schema),
        Selection::Remote { url, .. } => authority(url),
        Selection::Profile(resolved) => {
            let source = resolved.profile.history.as_ref().map(|h| &h.source);
            match source.and_then(|s| resolved.bindings.get(s)) {
                Some(binding) => match &binding.endpoint {
                    Endpoint::Http { url, .. } => authority(url),
                    Endpoint::File { root } => root.display().to_string(),
                    Endpoint::Postgres { schema, .. } => postgres(schema),
                },
                None => resolved.name.clone(),
            }
        }
    }
}
