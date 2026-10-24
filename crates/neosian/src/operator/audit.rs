//! Merge the memory ledger and one conversation's trail (DESIGN §20).

use std::io::{self, Write};

use clap::Args;
use serde_json::{Value, json};

use super::{Flags, finish};
use crate::cli::usage;
use crate::conversation::{Error, sessions};
use crate::home::project_scope;
use crate::memory::grammar::SCOPE_ENV;
use crate::memory::mounts::Refusal;
use crate::repr::repr;
use crate::state::State;
use crate::store::grammar::check_scope;
use crate::store::time::Stamp;

#[derive(Args)]
pub struct Audit {
    #[command(flatten)]
    flags: Flags,
    /// Scope to audit; defaults to NEOSIAN_SCOPE or this directory's project.
    #[arg(long)]
    scope: Option<String>,
    /// Merge this conversation's turns and erasure trail.
    #[arg(long)]
    conversation: Option<String>,
    /// Only this actor and writes under its prefix.
    #[arg(long)]
    actor: Option<String>,
    /// ISO-8601 with an offset; inclusive.
    #[arg(long)]
    since: Option<String>,
    /// The newest N entries.
    #[arg(long, allow_negative_numbers = true)]
    limit: Option<i64>,
}

pub fn run(args: &Audit, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let settings = || -> Result<_, String> {
        let since = args
            .since
            .as_deref()
            .map(|value| {
                Stamp::parse(value).map_err(|_| {
                    format!(
                        "argument --since: invalid _timestamp value: {}",
                        repr(value)
                    )
                })
            })
            .transpose()?;
        let selection = args.flags.selection()?;
        if let Some(limit) = args.limit.filter(|n| *n < 0) {
            return Err(format!("--limit must be >= 0, got {limit}"));
        }
        let scope = match args
            .scope
            .clone()
            .or_else(|| std::env::var(SCOPE_ENV).ok().filter(|v| !v.is_empty()))
        {
            Some(scope) => {
                check_scope(&scope).map_err(|reason| format!("--scope: {reason}"))?;
                scope
            }
            None => project_scope(&std::env::current_dir().map_err(|e| e.to_string())?).map_err(
                |error| match error {
                    Refusal::Usage(text) => text,
                    Refusal::Memory(error) => error.message,
                },
            )?,
        };
        Ok((selection, scope, since))
    };
    let (selection, scope, since) = match settings() {
        Ok(settings) => settings,
        Err(message) => return usage(args.flags.json, &message, out, err),
    };
    let answer = (|| {
        let store = selection.open()?;
        let mut entries = entries(store.as_ref(), &scope, args.conversation.as_deref(), since)?;
        if let Some(pattern) = &args.actor {
            entries.retain(|entry| actor_matches(entry.data["actor"].as_str(), pattern));
        }
        entries.sort_by_key(|entry| std::cmp::Reverse(entry.stamp.instant()));
        if let Some(limit) = args.limit {
            entries.truncate(limit as usize);
        }
        if args.flags.json {
            let entries: Vec<_> = entries.iter().map(|entry| &entry.data).collect();
            writeln!(
                out,
                "{}",
                json!({"scope":scope,"conversation_id":args.conversation,"client":store.client(),"entries":entries})
            )?;
        } else if entries.is_empty() {
            writeln!(out, "no ledger entries for scope {}", repr(&scope))?;
        } else {
            for entry in entries {
                writeln!(out, "{}", entry.line())?;
            }
        }
        Ok(())
    })();
    finish(answer, args.flags.json, out, err)
}

struct Entry {
    stamp: Stamp,
    data: Value,
}

impl Entry {
    fn new(stamp: Stamp, actor: Option<&str>, event: &str) -> Self {
        Self {
            stamp,
            data: json!({
                "created_at":stamp.z(),"actor":actor,"event":event,"path":null,
                "version":null,"redacted":false,"count":null,"conversation_id":null,
                "turn":null,"continues":[],"turns":[],
            }),
        }
    }

    fn line(&self) -> String {
        let data = &self.data;
        let text = |key: &str| data[key].as_str().unwrap_or_default();
        let event = text("event");
        let target = if event == "turn" {
            format!("{} turn {}", text("conversation_id"), data["turn"])
        } else if event == "redacted" && !data["conversation_id"].is_null() {
            let turns = data["turns"]
                .as_array()
                .expect("turns array")
                .iter()
                .map(|v| v.as_u64().expect("turn number"))
                .collect::<Vec<_>>();
            format!("{} turns {}", text("conversation_id"), turn_ranges(&turns))
        } else if event == "redacted" {
            let place = if data["path"].is_null() {
                "scope-wide".to_string()
            } else {
                format!("/{}", text("path"))
            };
            format!("{} at {place}", data["count"])
        } else {
            let marker = if data["redacted"] == true {
                " (redacted)"
            } else {
                ""
            };
            format!("/{} v{}{marker}", text("path"), data["version"])
        };
        let continued: Vec<_> = data["continues"]
            .as_array()
            .expect("continues array")
            .iter()
            .filter_map(Value::as_str)
            .collect();
        let tail = if continued.is_empty() {
            String::new()
        } else {
            format!("  continues {}", continued.join(", "))
        };
        let actor = data["actor"]
            .as_str()
            .filter(|s| !s.is_empty())
            .unwrap_or("-");
        format!("{}  {actor}  {event}  {target}{tail}", self.stamp.z())
    }
}

fn entries(
    store: &dyn State,
    scope: &str,
    conversation: Option<&str>,
    since: Option<Stamp>,
) -> Result<Vec<Entry>, Error> {
    let mut entries = Vec::new();
    for row in store.history(scope, since, None)? {
        let mut entry = Entry::new(row.created_at, row.actor.as_deref(), row.action.as_str());
        if row.path.starts_with(sessions::SESSIONS_PREFIX) && !row.redacted {
            entry.data["continues"] = json!(sessions::continues(&row.content));
        }
        entry.data["path"] = json!(row.path);
        entry.data["version"] = json!(row.version);
        entry.data["redacted"] = json!(row.redacted);
        entries.push(entry);
    }
    for act in store.redactions(scope, since, None)? {
        let mut entry = Entry::new(act.created_at, act.actor.as_deref(), "redacted");
        entry.data["path"] = json!(act.path);
        entry.data["count"] = json!(act.count);
        entries.push(entry);
    }
    if let Some(id) = conversation {
        for turn in store.read_turns(id, 0, None)? {
            if since.is_some_and(|since| turn.created_at.instant() < since.instant()) {
                continue;
            }
            let mut entry = Entry::new(turn.created_at, turn.actor.as_deref(), "turn");
            entry.data["conversation_id"] = json!(id);
            entry.data["turn"] = json!(turn.turn);
            entries.push(entry);
        }
        for act in store.turn_redactions(Some(&[id.to_string()]), since, 500)? {
            let mut entry = Entry::new(act.created_at, act.actor.as_deref(), "redacted");
            entry.data["conversation_id"] = json!(id);
            entry.data["count"] = json!(act.turns.len());
            entry.data["turns"] = json!(act.turns);
            entries.push(entry);
        }
    }
    Ok(entries)
}

fn actor_matches(actor: Option<&str>, pattern: &str) -> bool {
    let Some(actor) = actor else {
        return false;
    };
    let head = actor.split('#').next().unwrap_or_default();
    actor == pattern || head == pattern || head.starts_with(&format!("{pattern}/"))
}

fn turn_ranges(turns: &[u64]) -> String {
    let mut ranges = Vec::new();
    let mut rest = turns;
    while let Some((&first, tail)) = rest.split_first() {
        let mut last = first;
        rest = tail;
        while let Some((&next, tail)) = rest.split_first() {
            if last.checked_add(1) != Some(next) {
                break;
            }
            last = next;
            rest = tail;
        }
        ranges.push(if first == last {
            first.to_string()
        } else {
            format!("{first}-{last}")
        });
    }
    ranges.join(", ")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn actor_prefixes_stop_at_segments_and_turn_suffixes() {
        for actor in ["cli:writer", "cli:writer#7", "cli:writer/conv:x#2"] {
            assert!(actor_matches(Some(actor), "cli:writer"));
        }
        assert!(!actor_matches(Some("cli:writer-two"), "cli:writer"));
        assert!(actor_matches(Some("opaque actor"), "opaque actor"));
        assert!(!actor_matches(None, ""));
        assert!(!actor_matches(Some("cli:writer#7"), "cli:writer#2"));
    }

    #[test]
    fn turn_ranges_keep_gaps_visible() {
        assert_eq!(turn_ranges(&[1, 2, 3, 7, 9, 10]), "1-3, 7, 9-10");
        assert_eq!(turn_ranges(&[]), "");
        assert_eq!(turn_ranges(&[4]), "4");
    }
}
