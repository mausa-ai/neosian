//! `neosian records drafts [--discard]` (DESIGN §40.54 item 13): the
//! stale-draft sweep, fifty conversations a page, on a FileStore, a
//! PostgreSQL store or a server named with `--url`, always the store the
//! operator names and never a profile. Each conversation is printed as
//! one JSON line in the route's row shape, the same for every store and
//! for both output modes; a preview asks every page with `write: false`.
//! No page is trusted to end the loop: a page that names a next cursor
//! must hold a row and a cursor other than the one it was sent, each row
//! must come after the last one printed, and the answer must echo the
//! `write` it was asked. A refused or failed later page is returned after
//! the rows already printed, so it is reported beside them.

use std::io::{self, Write};

use crate::conversation::Error;
use crate::conversation::turn_records::sweep::SweepPage;
use crate::state::records::{Fault, Records, Source};

/// Conversations one page visits, the route's default.
const PAGE: usize = 50;

/// Every page of the sweep of `source`, removing when `discard`.
pub(super) fn drafts(source: &Source, discard: bool, out: &mut impl Write) -> Result<(), Error> {
    let Some(keeper) = source.keeper()? else {
        return Err(source.absent());
    };
    pages(|cursor| keeper.sweep(PAGE, cursor, discard), discard, out)
}

fn stalled(page: usize, why: &str) -> Error {
    Error::Value(format!(
        "page {page} of the sweep {why}; nothing more was asked"
    ))
}

/// The page loop over `page`, which asks for the page after a cursor.
fn pages(
    mut page: impl FnMut(Option<&str>) -> Result<SweepPage, Fault>,
    write: bool,
    out: &mut impl Write,
) -> Result<(), Error> {
    let (mut cursor, mut last) = (None::<String>, None::<String>);
    let mut number = 0;
    loop {
        number += 1;
        let answer = page(cursor.as_deref()).map_err(Fault::into_error)?;
        if answer.write != write {
            return Err(stalled(number, "did not echo the write it was asked"));
        }
        if answer.next_cursor.is_some() && answer.conversations.is_empty() {
            return Err(stalled(number, "named a next cursor without a row"));
        }
        if answer.next_cursor.is_some() && answer.next_cursor == cursor {
            return Err(stalled(number, "named the cursor it was sent"));
        }
        for row in answer.conversations {
            if last
                .as_ref()
                .is_some_and(|last| row.conversation_id <= *last)
            {
                return Err(stalled(number, "answered a conversation out of order"));
            }
            serde_json::to_writer(&mut *out, &row).map_err(io::Error::from)?;
            writeln!(out)?;
            last = Some(row.conversation_id);
        }
        match answer.next_cursor {
            None => return Ok(()),
            next => cursor = next,
        }
    }
}

#[cfg(test)]
#[path = "sweep_tests.rs"]
mod tests;
