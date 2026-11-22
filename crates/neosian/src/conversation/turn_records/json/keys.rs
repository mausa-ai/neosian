//! Keys repeated in an object (review finding 10 of 2481787): the codec
//! writes a key once per object, and serde_json keeps the last value of a
//! repeated one, so a record holding a repeat would read back otherwise
//! than it was written. The keys of each open object are held and compared
//! while all those held cost at most `KEYS` bytes, a key its text as
//! written and one; past that, the object whose key would pass the bound
//! stops being compared and lets its held keys go, and a repeat there is
//! stored as written (§40.54 says what a reader then gets). The bound keeps
//! a draft's state small: it is written after every part.

use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

/// The most bytes of keys the check holds.
const KEYS: usize = 4_096;

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub(super) struct Keys {
    /// One entry per open object, innermost last: its keys while compared.
    open: Vec<Option<BTreeSet<String>>>,
    /// What the held keys cost.
    held: usize,
    /// The key being read, while its object is compared.
    key: Option<String>,
}

fn cost(keys: &BTreeSet<String>) -> usize {
    keys.iter().map(|key| key.len() + 1).sum()
}

impl Keys {
    pub(super) fn open(&mut self) {
        self.open.push(Some(BTreeSet::new()));
    }

    pub(super) fn close(&mut self) {
        if let Some(Some(keys)) = self.open.pop() {
            self.held -= cost(&keys);
        }
    }

    /// A key begins in the innermost object.
    pub(super) fn start(&mut self) {
        self.key = match self.open.last() {
            Some(Some(_)) => Some(String::new()),
            _ => None,
        };
    }

    /// One character of the key as written; escapes are the codec's alone,
    /// so equal texts are equal keys.
    pub(super) fn push(&mut self, ch: char) {
        let Some(key) = self.key.as_mut() else {
            return;
        };
        key.push(ch);
        if self.held + key.len() + 1 > KEYS {
            self.key = None;
            if let Some(keys) = self.open.last_mut().and_then(Option::take) {
                self.held -= cost(&keys);
            }
        }
    }

    /// The key ended: refused when its object already holds it.
    pub(super) fn done(&mut self) -> Result<(), &'static str> {
        let (Some(key), Some(Some(keys))) = (self.key.take(), self.open.last_mut()) else {
            return Ok(());
        };
        let spent = key.len() + 1;
        if !keys.insert(key) {
            return Err("a key repeated in its object");
        }
        self.held += spent;
        Ok(())
    }
}
