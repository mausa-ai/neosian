//! What a lander reads before it starts a write for a queued turn
//! (rulings G1 and G2 of the T1e1 plan): before each part, each
//! `append_turn` (a repeat after a `Retry-After` wait too), each write of
//! the turn's state, its hold or removal, and its sessions document. The
//! turn on disk must still be the one it admitted, its file beginning with
//! the same opening line: a pause removes pending turns without the
//! conversation's lock, and a turn closed after it never takes the number
//! of one a lander holds (`queue.rs`). The conversation must not be
//! paused, and a profile turn's profile must still be served with its
//! capture on and the policy the turn was closed under (`serving.rs`). A
//! write already in flight may still be applied; nothing new starts. A
//! Stop's inline landing has no admission: it holds the session lock,
//! which a pause takes, and it lands no profile it resolved again.

use std::fs::File;
use std::io::{self, Read};
use std::path::Path;

use super::serving::Serving;

/// What the next write may do.
#[derive(Debug, PartialEq, Eq)]
pub enum Admit {
    Go,
    /// The conversation is paused, or the turn is no longer the one on
    /// disk: the pause removed it.
    Paused,
    /// The profile's capture is off, it is no longer served here, or its
    /// policy changed: the turn stays queued, and the next run decides.
    Off(String),
}

/// The queued turn a lander admitted: its file, its opening line, the
/// conversation's pause marker, and the destination as the lander serves
/// it.
pub struct Admission<'a> {
    pub turn: &'a Path,
    pub line: &'a str,
    pub paused: &'a Path,
    pub serving: Option<&'a Serving>,
}

impl Admission<'_> {
    /// Whether the turn on disk is still the one admitted: its file begins
    /// with the same opening line.
    pub fn ours(&self) -> io::Result<bool> {
        let mut file = match File::open(self.turn) {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
            Err(error) => return Err(error),
        };
        let mut head = vec![0; self.line.len() + 1];
        match file.read_exact(&mut head) {
            Ok(()) => Ok(head.strip_suffix(b"\n") == Some(self.line.as_bytes())),
            Err(error) if error.kind() == io::ErrorKind::UnexpectedEof => Ok(false),
            Err(error) => Err(error),
        }
    }

    /// What the conversation and its profile allow now, the turn aside.
    pub fn allows(&self) -> io::Result<Admit> {
        if self.paused.try_exists()? {
            return Ok(Admit::Paused);
        }
        Ok(match self.serving.and_then(Serving::changed) {
            Some(message) => Admit::Off(message),
            None => Admit::Go,
        })
    }

    /// What the next write may do: the turn still the one admitted, and
    /// what the conversation and its profile allow.
    pub fn admit(&self) -> io::Result<Admit> {
        match self.ours()? {
            true => self.allows(),
            false => Ok(Admit::Paused),
        }
    }
}
