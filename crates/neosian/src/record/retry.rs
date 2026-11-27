//! `neosian record retry [--json]` (DESIGN §40.54 item 17.5; ruling D9 of
//! the T1e1 plan): ends every wait and every block recorded for this
//! home's destinations (`landing/destination.rs`), so the next hook event
//! starts a lander for their queued turns and lands what its environment
//! serves. It sends nothing itself. Plain output names each destination
//! it unblocked by its host and what ended; `--json` prints one object,
//! `{"unblocked": [{"host", "blocked", "wait_until"}]}`, where `blocked`
//! is the block's reason or null and `wait_until` the wait's end or null.

use std::io::{self, Write};

use clap::Args;
use serde_json::json;

use super::landing::destination::unblock;
use super::landing::slots::directory;

/// Ends the waits and blocks of this home's destinations.
#[derive(Args)]
pub struct Retry {
    /// One JSON object on stdout.
    #[arg(long)]
    pub json: bool,
}

pub fn run(args: &Retry, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let ended = crate::home::home().and_then(|home| unblock(&directory(&home), |_| true, true));
    let ended = match ended {
        Ok(ended) => ended,
        Err(error) => {
            match args.json {
                true => writeln!(out, "{}", json!({"error": error.to_string()}))?,
                false => writeln!(err, "error: {error}")?,
            }
            return Ok(1);
        }
    };
    if args.json {
        let unblocked: Vec<_> = ended
            .iter()
            .map(|record| {
                let blocked = record.block.as_ref().map(|block| &block.reason);
                json!({"host": record.host, "blocked": blocked, "wait_until": record.wait_until})
            })
            .collect();
        writeln!(out, "{}", json!({ "unblocked": unblocked }))?;
        return Ok(0);
    }
    if ended.is_empty() {
        writeln!(out, "No destination was waiting or blocked.")?;
    }
    for record in &ended {
        let what = match (&record.block, &record.wait_until) {
            (Some(block), _) => format!("was blocked ({})", block.reason),
            (None, Some(until)) => format!("was waiting until {until}"),
            (None, None) => "was waiting".to_string(),
        };
        writeln!(
            out,
            "{}: {what}; its turns land at the next hook event",
            record.host
        )?;
    }
    Ok(0)
}
