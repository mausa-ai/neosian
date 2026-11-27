//! `status`'s recording lines (DESIGN §40.54 item 17.8; the T1e2b slice of
//! the T1e2 plan): what this machine keeps for each destination and what
//! the destination last answered, read from metadata only
//! (`record/landing/snapshot.rs`), with no store opened and no notice
//! consumed. A destination is listed while it is blocked, waits, fails or
//! has turns pending or held here, and the one a profile selected here
//! records to always, `nothing waiting` when all of it landed. Deletions by
//! the keep limit are home-wide. A home with nothing kept, recorded or
//! counted prints no recording line and `--json` has no `recording` key.
//! Unlike the session-start notices, these lines show what a server said,
//! as the lander kept it (cleaned and cut, ruling G3 of the T1e1 plan).

use std::collections::BTreeMap;
use std::io::{self, Write};
use std::path::Path;
use std::time::SystemTime;

use serde_json::{Value, json};

use crate::profile::resolve::Resolved;
use crate::record::landing::aside::label;
use crate::record::landing::destination::{Record, stamp};
use crate::record::landing::gate::host;
use crate::record::landing::kept::cut;
use crate::record::landing::notice::{limit, size};
use crate::record::landing::serving::profiled;
use crate::record::landing::snapshot::{Count, Place, take};
use crate::record::landing::state::{Destination, describe};
use crate::record::landing::usage::trees;
use crate::remote::selection::Selection;
use crate::store::time::Stamp;

const INDENT: &str = "           ";

/// The destination `selected` records to and its host, while its capture
/// is on in the home at `home`.
fn current(selected: Option<&Resolved>, home: &Path) -> Option<(Destination, String)> {
    let selected = selected?;
    let project = selected.project.to_string_lossy();
    let (resolved, _) = profiled(&selected.name, &project, home)?;
    let selection = Selection::Profile(Box::new(resolved));
    Some((describe(&selection), host(&selection)))
}

fn count(count: Count) -> Value {
    json!({"turns": count.turns, "bytes": count.bytes})
}

/// `at` when it is still ahead of `now`.
fn ahead(at: Option<&String>, now: Stamp) -> Option<String> {
    let at = at?;
    let later = Stamp::parse(at).is_ok_and(|at| at.instant() > now.instant());
    later.then(|| at.clone())
}

fn state(place: &Place, record: Option<&Record>, now: Stamp) -> &'static str {
    match record {
        Some(record) if record.block.is_some() => "blocked",
        Some(record) if record.failing_since.is_some() => "failing",
        Some(record) if ahead(record.wait_until.as_ref(), now).is_some() => "waiting",
        _ if !place.pending.is_empty() && place.served == Some(false) => "unserved",
        _ if !place.pending.is_empty() => "sending",
        _ if !place.held.is_empty() => "held",
        _ => "clear",
    }
}

/// How the destination is told apart from another of the same host.
fn detail(destination: Option<&Destination>) -> Value {
    json!(destination.map(|destination| match destination {
        Destination::Profile { name, project, .. } => format!("profile {name}, {project}"),
        Destination::Remote { url, .. } => url.clone(),
        other => label(other),
    }))
}

fn place(place: &Place, now: Stamp) -> Value {
    let record = place.record.as_ref();
    let blocked = record.and_then(|r| r.block.as_ref()).map(|block| {
        json!({"reason": block.reason, "status": block.status, "code": block.code,
            "message": cut(&block.message), "since": block.since,
            "next_attempt": block.next_attempt})
    });
    json!({"host": place.host, "key": place.key, "detail": detail(place.destination.as_ref()),
        "current": place.current, "state": state(place, record, now), "blocked": blocked,
        "wait_until": record.and_then(|r| ahead(r.wait_until.as_ref(), now)),
        "failing_since": record.and_then(|r| r.failing_since.clone()),
        "cause": record.and_then(|r| r.cause.as_deref().map(cut)),
        "served": place.served, "pending": count(Place::count(&place.pending)),
        "held": count(Place::count(&place.held))})
}

/// The `recording` object of `status --json` for the home at `home`, or
/// `None` when there is nothing to say.
pub fn report(home: &Path, selected: Option<&Resolved>, now: SystemTime) -> Option<Value> {
    let trees = trees(home, &home.join("spool"));
    let snapshot = take(home, &trees, None, current(selected, home));
    if snapshot.is_empty() {
        return None;
    }
    let now = stamp(now);
    let places: Vec<Value> = snapshot.places.iter().map(|p| place(p, now)).collect();
    let dropped = snapshot.drops.as_ref().map(|drops| {
        json!({"turns": drops.turns, "bytes": drops.bytes, "first": drops.first,
            "last": drops.last})
    });
    Some(
        json!({"limit_bytes": snapshot.limit, "destinations": places,
        "unattributed": {"pending": count(Place::count(&snapshot.loose.pending)),
            "held": count(Place::count(&snapshot.loose.held))},
        "dropped": dropped}),
    )
}

fn text(value: &Value) -> String {
    value
        .as_str()
        .unwrap_or_default()
        .chars()
        .map(|c| if c.is_control() { ' ' } else { c })
        .collect()
}

/// A stored time as `2026-11-21 14:03 UTC`; one that cannot be read as it is.
fn when(value: &Value) -> String {
    let raw = text(value);
    Stamp::parse(&raw).map_or(raw, |at| {
        let minutes = at.utc().minutes();
        format!(
            "{} UTC",
            minutes.trim_end_matches('Z').replacen('T', " ", 1)
        )
    })
}

fn turns(value: &Value) -> (u64, u64) {
    let number = |key: &str| value[key].as_u64().unwrap_or(0);
    (number("turns"), number("bytes"))
}

/// The first line's words for a destination.
fn phrase(place: &Value) -> String {
    let said = |key: &str, value: &Value| match text(value) {
        empty if empty.is_empty() => String::new(),
        words => format!("{key}{words}"),
    };
    let blocked = &place["blocked"];
    match place["state"].as_str().unwrap_or_default() {
        "blocked" => {
            let since = when(&blocked["since"]);
            match blocked["reason"].as_str() {
                Some("storage") => format!("paused: the account's storage is full (since {since})"),
                Some("credential") => format!(
                    "paused: it no longer accepts this machine's sign-in; run neosian login (since {since})"
                ),
                _ => format!(
                    "paused: it refuses writes (since {since}){}",
                    said(": ", &blocked["message"])
                ),
            }
        }
        "failing" => {
            let next = match place["wait_until"].is_string() {
                true => format!("; next attempt after {}", when(&place["wait_until"])),
                false => String::new(),
            };
            let cause = said(": ", &place["cause"]);
            format!(
                "failing since {}{cause}{next}",
                when(&place["failing_since"])
            )
        }
        "waiting" => format!(
            "waiting until {}{}",
            when(&place["wait_until"]),
            said(": ", &place["cause"])
        ),
        "unserved" => {
            "not served by this environment; a hook whose environment records there sends its turns"
                .into()
        }
        "sending" => "turns are sent from the next hook event".into(),
        "held" => "nothing waiting to be sent".into(),
        _ => "nothing waiting".into(),
    }
}

/// `kept on this machine: …`, or `None` when nothing is kept.
fn kept(pending: &Value, held: &Value, limit_bytes: &Value) -> Option<String> {
    let ((waiting, waiting_bytes), (held, held_bytes)) = (turns(pending), turns(held));
    let mut parts = Vec::new();
    if waiting > 0 {
        let noun = if waiting == 1 { "turn" } else { "turns" };
        parts.push(format!("{waiting} {noun} waiting, {}", size(waiting_bytes)));
    }
    if held > 0 {
        parts.push(format!(
            "{held} held, {} (neosian record held)",
            size(held_bytes)
        ));
    }
    if parts.is_empty() {
        return None;
    }
    if let Some(bytes) = limit_bytes.as_u64() {
        parts.push(format!("limit {}", limit(bytes)));
    }
    Some(format!("kept on this machine: {}", parts.join("; ")))
}

/// The recording lines of `status`'s text, from its `recording` object.
pub fn render(status: &Value, out: &mut impl Write) -> io::Result<()> {
    let Some(recording) = status.get("recording") else {
        return Ok(());
    };
    let places = recording["destinations"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let mut hosts: BTreeMap<String, usize> = BTreeMap::new();
    for place in &places {
        *hosts.entry(text(&place["host"])).or_default() += 1;
    }
    for place in &places {
        let host = text(&place["host"]);
        let shown = match (hosts[&host], place["detail"].as_str()) {
            (2.., Some(detail)) => format!("{host} ({})", text(&json!(detail))),
            _ => host,
        };
        writeln!(out, "recording  {shown}  {}", phrase(place))?;
        if let Some(kept) = kept(&place["pending"], &place["held"], &recording["limit_bytes"]) {
            writeln!(out, "{INDENT}{kept}")?;
        }
        if place["served"] == false && place["state"] != "unserved" {
            writeln!(
                out,
                "{INDENT}not served by this environment: a hook whose environment records there sends its turns"
            )?;
        }
    }
    let loose = &recording["unattributed"];
    if let Some(kept) = kept(&loose["pending"], &loose["held"], &Value::Null) {
        writeln!(out, "recording  {kept}, with no destination")?;
    }
    let dropped = &recording["dropped"];
    if dropped.is_object() {
        let (deleted, bytes) = turns(dropped);
        let noun = if deleted == 1 { "turn" } else { "turns" };
        let day = |key: &str| when(&dropped[key]).chars().take(10).collect::<String>();
        let (first, last) = (day("first"), day("last"));
        let days = match first == last {
            true => first,
            false => format!("{first} to {last}"),
        };
        let within = match recording["limit_bytes"].as_u64() {
            Some(bytes) => format!(" of {}", limit(bytes)),
            None => String::new(),
        };
        writeln!(
            out,
            "recording  deleted to stay within the limit{within}: {deleted} {noun}, {} ({days})",
            size(bytes)
        )?;
    }
    Ok(())
}
