//! A lander's ten minutes (DESIGN §40.54 items 17.3 and 23; A.5.16 of
//! the T1e1 plan), measured on the wall clock, which advances while a
//! machine sleeps, as `Instant` does not on macOS. A lander reads it before
//! each pass, each turn and each part, so the part in flight is finished
//! and no part starts after the cap. A clock set back before the start
//! ends the run too, so it never stretches one. What is left stays queued
//! for the next hook event. A Stop landing inline has no cap. `Halt` adds
//! a lander's admission of the turn, read before each write (`admit.rs`,
//! rulings E19, G1 and G2), a run's `Retry-After` budget (`Patience`) and
//! the one attempt at a blocked destination whose six hours have passed
//! (`gate.rs`).

use std::cell::Cell;
use std::io;
use std::time::{Duration, SystemTime};

use super::admit::{Admission, Admit};
use super::gate::Trial;
use super::rules::RETRY_SECONDS;

/// How long a lander runs at most.
pub const TEN_MINUTES: Duration = Duration::from_secs(600);

/// Why a run stopped at the cap, as its summary says it.
pub const PASSED: &str = "the lander's ten minutes passed; the next hook event resumes the rest";

#[derive(Clone, Copy)]
pub struct Cap {
    bound: Option<(SystemTime, fn() -> SystemTime)>,
}

impl Cap {
    /// No cap: a Stop's inline landing.
    pub const NONE: Self = Self { bound: None };

    /// Ten minutes from now on the system clock.
    pub fn ten_minutes() -> Self {
        Self::on(SystemTime::now)
    }

    /// Ten minutes from now on `clock`.
    pub fn on(clock: fn() -> SystemTime) -> Self {
        Self {
            bound: Some((clock(), clock)),
        }
    }

    /// The time on the cap's clock, the system's without a cap.
    pub fn now(&self) -> SystemTime {
        self.bound
            .map_or_else(SystemTime::now, |(_, clock)| clock())
    }

    /// Whether the run must start nothing more.
    pub fn passed(&self) -> bool {
        self.bound.is_some_and(|(started, clock)| {
            !clock()
                .duration_since(started)
                .is_ok_and(|run| run < TEN_MINUTES)
        })
    }
}

/// How long one lander's run waits out `Retry-After` answers of at most
/// 10 s before repeating the part: 30 s in all (DESIGN §40.54 items 17.3
/// and 23). Each wait counts at least a second, so a server answering
/// `Retry-After: 0` is asked at most 30 times. A wait past the budget is
/// not waited: the destination waits as after a longer `Retry-After`.
pub struct Patience {
    waited: Cell<u64>,
    sleep: fn(Duration),
}

/// The `Retry-After` waiting one run may do.
pub const PATIENCE_SECONDS: u64 = 3 * RETRY_SECONDS;

impl Patience {
    /// Waiting on the system's sleep.
    pub fn new() -> Self {
        Self::on(std::thread::sleep)
    }

    /// Waiting on `sleep`, for tests.
    pub fn on(sleep: fn(Duration)) -> Self {
        Self {
            waited: Cell::new(0),
            sleep,
        }
    }

    /// Waits `seconds` and says so when the budget allows it; otherwise
    /// waits nothing and says no.
    pub fn wait(&self, seconds: u64) -> bool {
        let seconds = seconds.max(1);
        let waited = self.waited.get() + seconds;
        if seconds > RETRY_SECONDS || waited > PATIENCE_SECONDS {
            return false;
        }
        self.waited.set(waited);
        (self.sleep)(Duration::from_secs(seconds));
        true
    }
}

impl Default for Patience {
    fn default() -> Self {
        Self::new()
    }
}

/// What ends a draft between parts: the cap; and what a lander adds: its
/// admission of the turn (`admit.rs`), its `Retry-After` budget, and the
/// attempt at a blocked destination, whose first part decides (17.5).
#[derive(Clone, Copy)]
pub struct Halt<'a> {
    pub cap: &'a Cap,
    pub admission: Option<&'a Admission<'a>>,
    pub patience: Option<&'a Patience>,
    pub trial: Option<&'a Trial<'a>>,
}

#[cfg(test)]
impl Halt<'static> {
    /// None of them, for tests.
    pub const NONE: Self = Self {
        cap: &Cap::NONE,
        admission: None,
        patience: None,
        trial: None,
    };
}

impl<'a> Halt<'a> {
    /// The attempt at a blocked destination while its first part is not
    /// answered yet.
    pub fn trying(&self) -> Option<&'a Trial<'a>> {
        self.trial.filter(|trial| trial.pending())
    }

    /// What the next write may do; anything without an admission goes.
    pub fn admit(&self) -> io::Result<Admit> {
        self.admission.map_or(Ok(Admit::Go), Admission::admit)
    }

    /// Whether the turn on disk is still the one admitted.
    pub fn ours(&self) -> io::Result<bool> {
        self.admission.map_or(Ok(true), Admission::ours)
    }

    /// What the conversation and its profile allow now, the turn aside.
    pub fn allows(&self) -> io::Result<Admit> {
        self.admission.map_or(Ok(Admit::Go), Admission::allows)
    }
}
