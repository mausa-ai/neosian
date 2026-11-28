//! What an import at login tells the person (the L1c slice of the L1 plan),
//! in L1a's step grammar: the offer in "Your memory", the transfer and its
//! result in "Import", and the JSON object the final line carries under
//! `import`. Every omission is named with its reason. Text a host sent (a
//! code, a reason, a time, a workspace) is shown only cleaned and cut.

use std::io::{self, Write};
use std::time::Instant;

use serde_json::{Value, json};

use super::drive::Outcome;
use super::journal::{Journal, Left, Unit};
use super::session::{Limits, Progress};
use super::transfer::{Sent, Why};
use crate::login::output::{Mark, View, fill, host as shown, say};

/// What a list of units holds.
pub(crate) struct Offered {
    pub scopes: usize,
    pub conversations: usize,
    pub bytes: u64,
}

impl Offered {
    pub(crate) fn of<'u>(units: impl IntoIterator<Item = &'u Unit>) -> Self {
        let mut made = Self {
            scopes: 0,
            conversations: 0,
            bytes: 0,
        };
        for unit in units {
            if unit.kind == "scope" {
                made.scopes += 1;
            } else {
                made.conversations += 1;
            }
            made.bytes += unit.bytes;
        }
        made
    }

    pub(crate) fn words(&self) -> [(&'static str, String); 3] {
        [
            ("scopes", count(self.scopes, "scope")),
            ("conversations", count(self.conversations, "conversation")),
            ("size", size(self.bytes)),
        ]
    }
}

/// `n` and its noun, singular or plural, from `login.yaml`.
pub(crate) fn count(n: usize, noun: &str) -> String {
    let form = if n == 1 { "one" } else { "many" };
    format!("{n} {}", say(&format!("noun_{noun}_{form}")))
}

/// Bytes for a person: binary units, one decimal.
pub(crate) fn size(bytes: u64) -> String {
    const UNITS: [&str; 4] = ["KiB", "MiB", "GiB", "TiB"];
    if bytes < 1024 {
        return format!("{bytes} bytes");
    }
    let mut value = bytes as f64 / 1024.0;
    let mut unit = 0;
    while value >= 1024.0 && unit + 1 < UNITS.len() {
        value /= 1024.0;
        unit += 1;
    }
    format!("{value:.1} {}", UNITS[unit])
}

pub(crate) fn filled(key: &str, words: &[(&str, String)]) -> String {
    let pairs: Vec<(&str, &str)> = words.iter().map(|(k, v)| (*k, v.as_str())).collect();
    fill(key, &pairs)
}

/// What the host admits, said once after Begin: eligibility.
pub(crate) fn limits(
    ui: &mut View<impl Write>,
    limits: Option<Limits>,
    journal: &Journal,
) -> io::Result<()> {
    let Some(l) = limits else { return Ok(()) };
    let words = [
        ("unit", size(l.unit_bytes)),
        ("storage", size(l.storage_bytes)),
        ("workspaces", l.workspaces.to_string()),
    ];
    ui.say(&filled("import_limits", &words))?;
    let over = journal
        .committed()
        .iter()
        .filter(|u| u.bytes > l.unit_bytes)
        .count();
    if over > 0 {
        ui.mark(
            Mark::Warn,
            &filled("import_over", &[("count", count(over, "unit"))]),
        )?;
    }
    Ok(())
}

/// Progress: a line for each unit with `--verbose`, else one every few
/// seconds; never a moving cursor.
pub(crate) fn progress(
    ui: &mut View<impl Write>,
    (done, total): (usize, usize),
    unit: &Unit,
    sent: &Sent,
    (verbose, last): (bool, &mut Instant),
) -> io::Result<()> {
    if verbose {
        let name = shown(&unit.name);
        let line = match sent {
            Sent::Stored => filled(
                "import_unit",
                &[("kind", unit.kind.clone()), ("name", name)],
            ),
            Sent::Omitted(reason) => omission(&unit.kind, &name, reason),
        };
        return ui.mark(Mark::Ok, &line);
    }
    if last.elapsed().as_secs() >= 5 && done < total {
        *last = Instant::now();
        let words = [("done", done.to_string()), ("total", count(total, "unit"))];
        ui.say(&filled("import_progress", &words))?;
    }
    Ok(())
}

fn omission(kind: &str, name: &str, reason: &str) -> String {
    let why = say(&format!("why_{reason}"));
    let words = [
        ("kind", kind.to_string()),
        ("name", name.to_string()),
        ("why", why),
    ];
    filled("import_omitted", &words)
}

/// Every unit left out, by name and reason: this machine's own, then the host's.
fn omissions(ui: &mut View<impl Write>, journal: &Journal) -> io::Result<()> {
    named(ui, journal.local.iter().chain(journal.host_omitted()))
}

/// Each unit left out, by name and with its own reason.
pub(crate) fn named<'l>(
    ui: &mut View<impl Write>,
    left: impl IntoIterator<Item = &'l Left>,
) -> io::Result<()> {
    for left in left {
        let line = omission(&left.kind, &shown(&left.name), &left.reason);
        ui.mark(Mark::Warn, &line)?;
    }
    Ok(())
}

pub(crate) fn why(why: &Why) -> String {
    match why {
        Why::Lost => say("stop_lost"),
        Why::Conflict(code) => fill("stop_conflict", &[("code", &shown(code))]),
        Why::Full(code) => fill("stop_full", &[("code", &shown(code))]),
        Why::Refused(status, code) => {
            let code = code.as_deref().map_or_else(|| "-".to_string(), shown);
            fill(
                "stop_refused",
                &[("status", &status.to_string()), ("code", &code)],
            )
        }
        Why::Local(text) => fill("stop_local", &[("text", &shown(text))]),
        Why::Mismatch(code) => fill("stop_mismatch", &[("code", &shown(code))]),
    }
}

fn host_wait(p: &Progress) -> String {
    let reason = p
        .waiting_reason
        .as_deref()
        .map_or_else(|| p.status.clone(), shown);
    let until = p.resume_at.as_deref().map_or_else(String::new, |t| {
        fill("import_until", &[("time", &shown(t))])
    });
    fill(
        "import_continues",
        &[("reason", &reason), ("until", &until)],
    )
}

/// The result in text; the actions it leaves for Next.
pub(crate) fn outcome(
    ui: &mut View<impl Write>,
    outcome: &Outcome,
    journal: &Journal,
) -> io::Result<Vec<String>> {
    let imported = Offered::of(journal.committed());
    let mut next = Vec::new();
    match outcome {
        Outcome::Succeeded(targets) => {
            ui.mark(Mark::Ok, &filled("import_succeeded", &imported.words()))?;
            for t in targets {
                let (source, profile) = (shown(&t.source), t.profile.as_deref().map(shown));
                let line = match (t.mount.as_str(), profile) {
                    ("workspace", Some(p)) => {
                        fill("target_workspace", &[("source", &source), ("profile", &p)])
                    }
                    _ => fill("target_me", &[("source", &source)]),
                };
                ui.say(&line)?;
            }
        }
        Outcome::Continuing(p) => {
            ui.mark(Mark::Ok, &filled("import_sent", &imported.words()))?;
            ui.say(&host_wait(p))?;
            next.push(say("next_check_import"));
        }
        Outcome::NotAuthorized => ui.mark(Mark::Warn, &say("import_unauthorized"))?,
        Outcome::Failed(p, retry) => {
            let code = p
                .error_code
                .as_deref()
                .map_or_else(|| "-".to_string(), shown);
            ui.mark(Mark::Fail, &fill("import_failed", &[("code", &code)]))?;
            if let Some((kind, name)) = &p.failed_unit {
                ui.say(&fill(
                    "import_failed_unit",
                    &[("kind", &shown(kind)), ("name", &shown(name))],
                ))?;
            }
            if *retry {
                next.push(say("next_import_again"));
            }
        }
        Outcome::Nothing => ui.mark(Mark::Warn, &say("import_nothing"))?,
        Outcome::Ended => ui.mark(Mark::Warn, &say("import_ended"))?,
        Outcome::Stopped(reason) => {
            ui.mark(
                Mark::Fail,
                &fill("import_stopped", &[("why", &why(reason))]),
            )?;
            next.push(say("next_resume_import"));
        }
    }
    omissions(ui, journal)?;
    Ok(next)
}

/// The `import` object of the JSON line.
pub(crate) fn json(outcome: &Outcome, journal: &Journal) -> Value {
    let units: Vec<Value> = journal
        .committed()
        .iter()
        .map(|u| json!({"kind": u.kind, "name": u.name, "sha256": u.sha256, "bytes": u.bytes}))
        .collect();
    let omitted: Vec<Value> = (journal.local.iter().chain(journal.host_omitted()))
        .map(|l| json!({"kind": l.kind, "name": l.name, "reason": l.reason}))
        .collect();
    let (status, progress) = match outcome {
        Outcome::Succeeded(_) => ("succeeded", None),
        Outcome::Continuing(p) => (p.status.as_str(), Some(p)),
        Outcome::NotAuthorized => ("not_authorized", None),
        Outcome::Failed(p, _) => ("failed", Some(p)),
        Outcome::Nothing => ("nothing", None),
        Outcome::Ended => ("ended", None),
        Outcome::Stopped(_) => ("stopped", None),
    };
    let targets = match outcome {
        Outcome::Succeeded(t) => t
            .iter()
            .map(|t| json!({"source": t.source, "mount": t.mount, "profile": t.profile}))
            .collect::<Vec<_>>(),
        _ => Vec::new(),
    };
    json!({
        "status": status,
        "session": journal.session.as_ref().map(|s| &s.id),
        "units": units,
        "omitted": omitted,
        "targets": targets,
        "error_code": progress.and_then(|p| p.error_code.as_ref()),
        "waiting_reason": progress.and_then(|p| p.waiting_reason.as_ref()),
        "resume_at": progress.and_then(|p| p.resume_at.as_ref()),
        "stopped": match outcome { Outcome::Stopped(w) => Value::from(why(w)), _ => Value::Null },
        "kept": !outcome.terminal(),
    })
}
