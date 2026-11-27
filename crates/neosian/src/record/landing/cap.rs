//! A lander's ten minutes (DESIGN §40.54 items 17.3 and 23; A.5.16 of
//! the T1e1 plan), measured on the wall clock, which advances while a
//! machine sleeps, as `Instant` does not on macOS. A lander reads it before
//! each pass, each turn and each part, so the part in flight is finished
//! and no part starts after the cap. A clock set back before the start
//! ends the run too, so it never stretches one. What is left stays queued
//! for the next hook event. A Stop landing inline has no cap. `Halt` adds
//! the conversation's pause marker, read before each part too (ruling E19).

use std::io;
use std::path::Path;
use std::time::{Duration, SystemTime};

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

    /// Whether the run must start nothing more.
    pub fn passed(&self) -> bool {
        self.bound.is_some_and(|(started, clock)| {
            !clock()
                .duration_since(started)
                .is_ok_and(|run| run < TEN_MINUTES)
        })
    }
}

/// What ends a draft between parts: the cap, and the conversation's pause
/// marker, `<id>.paused` beside its spool (ruling E19).
#[derive(Clone, Copy)]
pub struct Halt<'a> {
    pub cap: &'a Cap,
    pub paused: Option<&'a Path>,
}

#[cfg(test)]
impl Halt<'static> {
    /// Neither, for tests.
    pub const NONE: Self = Self {
        cap: &Cap::NONE,
        paused: None,
    };
}

impl Halt<'_> {
    /// Whether the conversation's pause marker is there now.
    pub fn paused(&self) -> io::Result<bool> {
        self.paused.map_or(Ok(false), Path::try_exists)
    }
}
