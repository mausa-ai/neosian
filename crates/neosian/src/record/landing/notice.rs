//! The session-start notices (DESIGN §40.54 item 17.8; the T1e2b slice and
//! the owner rulings of the T1e2 plan): one line for each change of state
//! the person was not told yet, built from the fixed templates in
//! `assets/prompts/context.yaml` with only safe values filled in: a host,
//! counts, sizes, the keep limit and held reason keywords. What a server
//! said is never put in a prompt, where it would be an injection path;
//! `neosian status` shows it.
//!
//! Told are an active block, the end of a block that was told, a day of
//! failures while it lasts, newly held turns, pending turns this process's
//! environment does not serve, and new drops, home-wide. A wait is not
//! told (`neosian status` shows its time), nor a block that began and
//! ended between two starts. At most `MOST` lines go into one start; the
//! rest wait for the next.

use super::keep;
use super::snapshot::{Count, Place, Snapshot, Turn};
use super::told::{self, Claim, Told};
use crate::assets::{CONTEXT, folded, render};
use crate::store::time::Stamp;

/// The most notices one session start shows.
pub const MOST: usize = 8;
/// A run of failures is told once it has lasted this long (17.4).
const DAY: i64 = 86_400;
/// The most of a host a notice shows, in scalars.
const HOST_SCALARS: usize = 120;

/// One line to show and what it tells.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Notice {
    pub line: String,
    pub claim: Claim,
}

fn template(key: &str) -> String {
    folded(CONTEXT, key).expect("context.yaml carries the recording notices")
}

/// `bytes` as a person reads it: bytes below a KiB, else one decimal of
/// the largest binary unit it reaches, up to GiB.
pub fn size(bytes: u64) -> String {
    const UNITS: [&str; 3] = ["KiB", "MiB", "GiB"];
    if bytes < 1024 {
        return format!("{bytes} {}", if bytes == 1 { "byte" } else { "bytes" });
    }
    let (mut value, mut unit) = (bytes as f64 / 1024.0, 0);
    while value >= 1024.0 && unit < UNITS.len() - 1 {
        value /= 1024.0;
        unit += 1;
    }
    format!("{value:.1} {}", UNITS[unit])
}

/// The keep limit as a person reads it: whole MiB or GiB when it is one.
pub fn limit(bytes: u64) -> String {
    match bytes {
        b if b > 0 && b % (1 << 30) == 0 => format!("{} GiB", b >> 30),
        b if b > 0 && b % (1 << 20) == 0 => format!("{} MiB", b >> 20),
        b => size(b),
    }
}

/// A host as a notice may show it: no control character, at most
/// `HOST_SCALARS` scalars.
fn safe(host: &str) -> String {
    host.chars()
        .filter(|c| !c.is_control())
        .take(HOST_SCALARS)
        .collect()
}

fn kept(count: Count) -> String {
    let size = size(count.bytes);
    match count.turns {
        1 => render(&template("notice_turn"), &[("size", &size)]),
        n => render(
            &template("notice_turns"),
            &[("count", &n.to_string()), ("size", &size)],
        ),
    }
}

fn line(key: &str, host: &str, count: Count, reasons: &str) -> String {
    let variables = [
        ("host", safe(host)),
        ("kept", kept(count)),
        ("reasons", reasons.to_string()),
    ];
    let pairs: Vec<(&str, &str)> = variables.iter().map(|(k, v)| (*k, v.as_str())).collect();
    render(&template(key), &pairs)
}

/// The block reason a record names, as 17.4 knows them (`gate::named`).
fn reason(name: &str) -> &'static str {
    match name {
        "storage" => "storage",
        "credential" => "credential",
        _ => "forbidden",
    }
}

/// The turns of `turns` not named in `told`, their count and reasons.
fn untold<'a>(turns: &'a [Turn], told: &std::collections::BTreeSet<String>) -> Vec<&'a Turn> {
    turns
        .iter()
        .filter(|turn| !told.contains(&turn.name))
        .collect()
}

fn names(turns: &[&Turn]) -> Vec<String> {
    turns.iter().map(|turn| turn.name.clone()).collect()
}

fn count(turns: &[&Turn]) -> Count {
    Count {
        turns: turns.len() as u64,
        bytes: turns.iter().map(|turn| turn.bytes).sum(),
    }
}

/// A notice of newly held turns, with `place`'s host when it has one.
fn held(place: &Place, turns: &[&Turn]) -> Notice {
    let mut reasons: Vec<&str> = turns.iter().filter_map(|turn| turn.reason).collect();
    reasons.sort_unstable();
    reasons.dedup();
    let key = match (place.key.is_empty(), reasons.as_slice()) {
        (true, _) => "notice_set_aside",
        (false, ["refused"]) => "notice_refused",
        (false, _) => "notice_held",
    };
    Notice {
        line: line(key, &place.host, count(turns), &reasons.join(", ")),
        claim: Claim::Held(names(turns)),
    }
}

/// Whether a run of failures that began at `since` has lasted a day at `now`.
fn a_day(since: &str, now: Stamp) -> bool {
    Stamp::parse(since).is_ok_and(|since| now.instant() - since.instant() >= DAY * 1_000_000)
}

/// What `snapshot` has to tell that `told` does not hold, in the order
/// shown: blocks, ends of blocks, days of failures, held turns, unserved
/// turns, drops. `now` is the clock of the process that tells.
pub fn fresh(snapshot: &Snapshot, told: &Told, now: Stamp) -> Vec<Notice> {
    let mut blocks = Vec::new();
    let mut failing = Vec::new();
    let mut held_ = Vec::new();
    let mut unserved = Vec::new();
    for place in &snapshot.places {
        let waiting = Place::count(&place.pending);
        let record = place.record.as_ref();
        if let Some(block) = record.and_then(|record| record.block.as_ref()) {
            let known = told.blocks.iter().any(|old| {
                old.key == place.key && old.reason == block.reason && old.since == block.since
            });
            if !known {
                let key = format!("notice_{}", reason(&block.reason));
                blocks.push(Notice {
                    line: line(&key, &place.host, waiting, ""),
                    claim: Claim::Block(told::Block {
                        key: place.key.clone(),
                        host: safe(&place.host),
                        reason: block.reason.clone(),
                        since: block.since.clone(),
                    }),
                });
            }
        } else if let Some(since) = record.and_then(|record| record.failing_since.as_ref())
            && a_day(since, now)
            && !told
                .failing
                .iter()
                .any(|old| old.key == place.key && old.since == *since)
        {
            failing.push(Notice {
                line: line("notice_failing", &place.host, waiting, ""),
                claim: Claim::Failing(told::Failing {
                    key: place.key.clone(),
                    since: since.clone(),
                }),
            });
        }
        let new = untold(&place.held, &told.held);
        if !new.is_empty() {
            held_.push(held(place, &new));
        }
        let new = untold(&place.pending, &told.unserved);
        if place.served == Some(false) && !new.is_empty() {
            unserved.push(Notice {
                line: line("notice_unserved", &place.host, count(&new), ""),
                claim: Claim::Unserved(names(&new)),
            });
        }
    }
    let ended = told.blocks.iter().filter(|old| {
        !snapshot.places.iter().any(|place| {
            place.key == old.key && place.record.as_ref().is_some_and(|r| r.block.is_some())
        })
    });
    let ended: Vec<Notice> = ended
        .map(|old| Notice {
            line: line("notice_unblocked", &old.host, Count::default(), ""),
            claim: Claim::Unblock(old.key.clone()),
        })
        .collect();
    let new = untold(&snapshot.loose.held, &told.held);
    if !new.is_empty() {
        held_.push(held(&snapshot.loose, &new));
    }
    let mut notices = blocks;
    notices.extend(ended);
    notices.extend(failing);
    notices.extend(held_);
    notices.extend(unserved);
    notices.extend(dropped(snapshot, told));
    notices
}

/// New drops: what `drops.json` counts beyond what was told, all of it
/// when its counts started again (another `first`, or fewer than told).
fn dropped(snapshot: &Snapshot, told: &Told) -> Option<Notice> {
    let drops = snapshot.drops.as_ref()?;
    let base = told.dropped.as_ref().filter(|old| {
        old.first == drops.first && old.turns <= drops.turns && old.bytes <= drops.bytes
    });
    let (turns, bytes) = base.map_or((drops.turns, drops.bytes), |old| {
        (drops.turns - old.turns, drops.bytes - old.bytes)
    });
    if turns == 0 && bytes == 0 {
        return None;
    }
    let limit = limit(snapshot.limit.unwrap_or(keep::DEFAULT));
    let text = match turns {
        0 => render(
            &template("notice_dropped_bytes"),
            &[("limit", &limit), ("size", &size(bytes))],
        ),
        _ => render(
            &template("notice_dropped"),
            &[("limit", &limit), ("kept", &kept(Count { turns, bytes }))],
        ),
    };
    Some(Notice {
        line: text,
        claim: Claim::Dropped(told::Dropped {
            first: drops.first.clone(),
            turns: drops.turns,
            bytes: drops.bytes,
        }),
    })
}

/// The block `notices` make at the head of the session-start context: the
/// header line, then one line each.
pub fn block(notices: &[Notice]) -> String {
    let mut lines = vec![template("notice_header")];
    lines.extend(notices.iter().map(|notice| notice.line.clone()));
    lines.join("\n")
}

/// The first of `notices`, at most `MOST`, whose block and the blank line
/// after it fit `room` characters.
pub fn fit(notices: Vec<Notice>, room: usize) -> Vec<Notice> {
    let mut kept = Vec::new();
    for notice in notices.into_iter().take(MOST) {
        kept.push(notice);
        if crate::text::len(&block(&kept)) + 2 > room {
            kept.pop();
            break;
        }
    }
    kept
}
