//! The deterministic erasers (DESIGN §38), including sessions documents
//! across scopes and a prune plan based on each conversation's newest turn.

use std::io::{self, Write};

use clap::Args;
use serde_json::{Value, json};

use super::{Flags, actor, count, finish};
use crate::cli::usage;
use crate::conversation::Error;
use crate::conversation::ids::check_conversation_id;
use crate::conversation::sessions::{SESSIONS_PREFIX, sessions_path};
use crate::repr::repr;
use crate::state::State;
use crate::store::grammar::Actor;
use crate::store::time::Stamp;

#[derive(Args)]
pub struct Redact {
    /// The conversation id.
    #[arg(value_name = "CONVERSATION")]
    conversation: String,
    /// Every turn, and its sessions documents in every scope.
    #[arg(long)]
    all: bool,
    /// Turns 1 to N.
    #[arg(long, allow_negative_numbers = true)]
    through: Option<i64>,
    /// These turn numbers, separated by commas.
    #[arg(long, allow_hyphen_values = true)]
    turns: Option<String>,
    /// Who erases.
    #[arg(long, default_value = "cli:local")]
    actor: String,
    #[command(flatten)]
    flags: Flags,
}

#[derive(Args)]
pub struct Prune {
    /// Conversations whose newest turn is older than this (e.g. 30d or 12h).
    #[arg(long, value_name = "<N>d|<N>h", allow_hyphen_values = true)]
    older_than: String,
    /// Report the plan without erasing.
    #[arg(long)]
    dry_run: bool,
    /// Who erases.
    #[arg(long, default_value = "cli:local")]
    actor: String,
    #[command(flatten)]
    flags: Flags,
}

fn numbers(value: &str) -> Result<Vec<i64>, String> {
    let numbers = value
        .split(',')
        .map(|part| part.trim().parse::<i64>())
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| {
            format!(
                "argument --turns: not turn numbers: {} (e.g. 3 or 3,7)",
                repr(value)
            )
        })?;
    if numbers.iter().any(|n| *n < 1) {
        return Err(format!(
            "argument --turns: turn numbers start at 1: {}",
            repr(value)
        ));
    }
    Ok(numbers)
}

fn duration(value: &str) -> Result<i64, String> {
    let invalid = || {
        format!(
            "argument --older-than: not a duration: {} (e.g. 30d or 12h)",
            repr(value)
        )
    };
    let factor = match value.as_bytes().last() {
        Some(b'd') => 86400,
        Some(b'h') => 3600,
        _ => return Err(invalid()),
    };
    let digits = &value[..value.len() - 1];
    if digits.is_empty() || digits.starts_with('0') || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return Err(invalid());
    }
    digits
        .parse::<i64>()
        .ok()
        .and_then(|n| n.checked_mul(factor))
        .filter(|n| *n <= i64::MAX / 1_000_000)
        .ok_or_else(invalid)
}

pub fn redact(args: &Redact, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let settings = || -> Result<_, String> {
        let turns = args.turns.as_deref().map(numbers).transpose()?;
        let selection = args.flags.selection()?;
        let actor = actor(&args.actor)?;
        check_conversation_id(&args.conversation).map_err(|e| format!("CONVERSATION: {e}"))?;
        let chosen = usize::from(args.all)
            + usize::from(args.through.is_some_and(|n| n != 0))
            + usize::from(turns.is_some());
        if chosen != 1 {
            return Err("give exactly one of --all, --through N or --turns N[,N...]".to_string());
        }
        if let Some(n) = args.through.filter(|n| *n < 1) {
            return Err(format!("--through must be >= 1, got {n}"));
        }
        Ok((selection, actor, turns))
    };
    let (selection, actor, turns) = match settings() {
        Ok(settings) => settings,
        Err(message) => return usage(args.flags.json, &message, out, err),
    };
    let answer = (|| {
        let store = selection.open()?;
        let count = store.redact_turns(
            &args.conversation,
            args.through,
            turns.as_deref(),
            Some(actor.as_str()),
        )?;
        let documents = if args.all {
            sessions(
                store.as_ref(),
                std::slice::from_ref(&args.conversation),
                &actor,
                true,
            )?
        } else {
            Vec::new()
        };
        let envelope = json!({"verb":"redact","conversation_id":args.conversation,"count":count,"documents":documents,"client":store.client()});
        render(&envelope, args.flags.json, out)
    })();
    finish(answer, args.flags.json, out, err)
}

pub fn prune(args: &Prune, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let settings = || -> Result<_, String> {
        let duration = duration(&args.older_than)?;
        Ok((args.flags.selection()?, actor(&args.actor)?, duration))
    };
    let (selection, actor, duration) = match settings() {
        Ok(settings) => settings,
        Err(message) => return usage(args.flags.json, &message, out, err),
    };
    let answer = (|| {
        let store = selection.open()?;
        let cutoff = Stamp::now().after(-duration);
        let mut plan = Vec::new();
        let mut ids = Vec::new();
        let mut turns = 0;
        for id in store.conversations()? {
            let last = store.last_turn_number(&id)?;
            if last == 0 {
                continue;
            }
            let newest = store.read_turns(&id, (last - 1) as i64, Some(1))?;
            let newest = newest
                .first()
                .ok_or_else(|| Error::Value("the newest turn is missing".to_string()))?;
            if newest.created_at.instant() >= cutoff.instant() {
                continue;
            }
            if store
                .read_turns(&id, 0, None)?
                .iter()
                .all(|turn| turn.redacted)
            {
                continue;
            }
            plan.push(
                json!({"conversation_id":id,"turns":last,"last_at":stamp(newest.created_at)}),
            );
            turns += last as usize;
            ids.push(id);
        }
        if !args.dry_run {
            turns = 0;
            for id in &ids {
                turns += store.redact_turns(id, None, None, Some(actor.as_str()))?;
            }
        }
        let documents = sessions(store.as_ref(), &ids, &actor, !args.dry_run)?;
        let envelope = json!({
            "verb": "prune", "cutoff": stamp(cutoff), "dry_run": args.dry_run,
            "turns": turns, "conversations": plan, "documents": documents,
            "client": store.client(),
        });
        render(&envelope, args.flags.json, out)
    })();
    finish(answer, args.flags.json, out, err)
}

fn sessions(
    store: &dyn State,
    ids: &[String],
    actor: &Actor,
    write: bool,
) -> Result<Vec<Value>, Error> {
    if ids.is_empty() {
        return Ok(Vec::new());
    }
    let wanted: Vec<_> = ids.iter().map(|id| sessions_path(id)).collect();
    let mut found = Vec::new();
    for scope in store.scopes()? {
        for entry in store.list_documents(&scope, SESSIONS_PREFIX)? {
            if wanted.contains(&entry.path) && !entry.redacted {
                if write {
                    store.redact(&scope, Some(&entry.path), actor)?;
                }
                found.push(json!({"scope":scope,"path":entry.path}));
            }
        }
    }
    Ok(found)
}

fn stamp(value: Stamp) -> String {
    format!("{}Z", &value.utc().iso()[..19])
}

fn render(envelope: &Value, json: bool, out: &mut impl Write) -> Result<(), Error> {
    if json {
        writeln!(out, "{envelope}")?;
        return Ok(());
    }
    let text = |data: &Value, key: &str| data[key].as_str().unwrap_or_default().to_string();
    let n = |data: &Value, key: &str| data[key].as_u64().unwrap_or_default() as usize;
    let did = if envelope["dry_run"] == true {
        "would redact"
    } else {
        "redacted"
    };
    if envelope["verb"] == "redact" {
        let matched = n(envelope, "count");
        let id = text(envelope, "conversation_id");
        if matched == 0 {
            writeln!(out, "no turns matched in {id}")?;
        } else {
            writeln!(out, "redacted {} of {id}", count(matched, "turn"))?;
        }
    } else {
        let plan = envelope["conversations"].as_array().expect("prune plan");
        let cutoff = text(envelope, "cutoff");
        if plan.is_empty() {
            writeln!(out, "nothing older than {cutoff}")?;
        } else {
            for row in plan {
                writeln!(
                    out,
                    "{}  {}  last {}",
                    text(row, "conversation_id"),
                    count(n(row, "turns"), "turn"),
                    text(row, "last_at")
                )?;
            }
            writeln!(
                out,
                "{did} {}, {} older than {cutoff}",
                count(plan.len(), "conversation"),
                count(n(envelope, "turns"), "turn")
            )?;
        }
    }
    for document in envelope["documents"]
        .as_array()
        .expect("sessions documents")
    {
        writeln!(
            out,
            "{did} /{} in {}",
            text(document, "path"),
            text(document, "scope")
        )?;
    }
    Ok(())
}
