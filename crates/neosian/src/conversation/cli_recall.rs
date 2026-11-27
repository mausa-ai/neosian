//! `neosian recall` (DESIGN §33, §40.54 item 19, #448; the T1f plan's
//! ruling on its grammar): one turn of a recorded conversation as
//! `recall_turn` shows it, by the same rules and the same words. `--view`
//! picks the view, `light` by default; `--call` or `--seq` opens one piece
//! and `--part` picks its part, counted from 0. Exit tiers (§14.1): 2 for
//! grammar with nothing constructed, including a request the rules
//! refuse; 1 when the store answers with an error or a corrective
//! failure; 0 with the text.

use std::io::{self, Write};
use std::path::PathBuf;

use clap::Args;

use super::failed;
use super::ids::check_conversation_id;
use super::recall::{Ask, View, recall};
use crate::cli::usage;
use crate::remote::selection::Selection;
use crate::repr::repr;
use crate::state::records::HistoryReader;

/// Show one turn of a recorded conversation, or one part of a piece of it.
#[derive(Args)]
pub struct Recall {
    /// the conversation id, as its sessions document names it
    conversation: String,
    /// the turn number, from 1
    #[arg(allow_negative_numbers = true)]
    turn: i64,
    /// how to show the turn: light (default), prompts, calls or full
    #[arg(long, value_enum)]
    view: Option<View>,
    /// open the result of this tool call, one part at a time
    #[arg(long)]
    call: Option<String>,
    /// open the piece holding this record, one part at a time
    #[arg(long, allow_negative_numbers = true)]
    seq: Option<i64>,
    /// which part of the piece --call or --seq opens, from 0 (default 0)
    #[arg(long, allow_negative_numbers = true)]
    part: Option<i64>,
    /// FileStore root directory (created on start; default: the home,
    /// ~/.neosian or $NEOSIAN_HOME)
    #[arg(long)]
    root: Option<PathBuf>,
    /// State process URL; token from NEOSIAN_CLIENT_TOKEN.
    #[arg(long)]
    url: Option<String>,
    /// Postgres schema (Postgres only).
    #[arg(long)]
    schema: Option<String>,
    #[arg(long)]
    profile: Option<String>,
}

pub fn run(args: &Recall, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let selected = match Selection::resolve_profile(
        args.root.as_deref(),
        args.url.as_deref(),
        args.schema.as_deref(),
        args.profile.as_deref(),
        &std::env::current_dir()?,
        false,
    ) {
        Ok(selected) => selected,
        Err(message) => return usage(false, &message, out, err),
    };
    if let Err(reason) = check_conversation_id(&args.conversation) {
        let message = format!("conversation {}: {reason}", repr(&args.conversation));
        return usage(false, &message, out, err);
    }
    let checked = Ask::new(
        &args.conversation,
        args.turn,
        args.view,
        args.call.as_deref(),
        args.seq,
        args.part,
    );
    let ask = match checked {
        Ok(ask) => ask,
        Err(refused) => {
            writeln!(err, "error: {}", refused.error)?;
            if let Some(hint) = refused.hint {
                writeln!(err, "hint: {hint}")?;
            }
            return Ok(2);
        }
    };
    let history = match HistoryReader::open(&selected) {
        Ok(history) => history,
        Err(error) => return failed(&super::Error::from(error).text(), None, false, out, err),
    };
    let records = match history.records() {
        Ok(records) => records,
        Err(error) => return failed(&error.text(), None, false, out, err),
    };
    let label = format!("Conversation {}", repr(&args.conversation));
    let store = history.state();
    match recall(store.as_ref(), records.as_deref(), &ask, &label) {
        Ok(Ok(recalled)) => {
            writeln!(out, "{}", recalled.text)?;
            Ok(0)
        }
        Ok(Err(refused)) => failed(&refused.error, refused.hint.as_deref(), false, out, err),
        Err(error) => failed(&error.text(), None, false, out, err),
    }
}
