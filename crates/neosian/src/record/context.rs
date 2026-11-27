//! The read side of `neosian record` (`record/context.py`, DESIGN §21.7,
//! §33): the memory index, the pending handoff note and "where we left
//! off", the scope's recent sessions log-projected the way a view is, so
//! `recall_turn(n, conversation=…)` re-reads any of them verbatim. Never
//! a write, never the spool. `source: compact` is the re-injection after
//! the client paged its own history, so the own session's record is what
//! comes back, and no note. One budget per client at the output edge,
//! shared by the three blocks (and Pi's memory instructions). On a store
//! that keeps records the sessions are read as light records
//! (`left_off.rs`).

mod left_off;

pub use left_off::{History, render_left_off};

use crate::assets::{CONTEXT, MEMORY, MESSAGES, block, folded, literal, render};
use crate::conversation::Error;
use crate::conversation::sessions::{HANDOFF_PATH, Tier, handoff_tier, parse_handoff};
use crate::memory::index::{render as render_index, render_within};
use crate::memory::mounts::Mount;
use crate::messaging::delivery::context as mailbox_context;
use crate::messaging::mailbox::Mailbox;
use crate::state::State;
use crate::store::time::Stamp;
use crate::text::{len, rstrip};

pub const COMPACT_SOURCE: &str = "compact";
const DEFAULT_CONTEXT_CHARS: isize = 10_000;
/// `LEFT_OFF_BUDGET_CHARS`: the index budget, the start blocks' at full.
const LEFT_OFF_BUDGET_CHARS: isize = 8192;

/// What each client injects whole from a SessionStart hook (verified
/// 2026-10-03, §33): Claude Code's documented 10,000 chars; Codex's 2,500
/// tokens at the §6 heuristic with margin; Cursor's undocumented cap at
/// the conservative figure; Muse's 16 KiB, cut again in bytes at the edge;
/// Pi's is neosian's own section budget, not a Pi limit.
pub fn context_chars(agent: &str) -> isize {
    match agent {
        "claude-code" | "cursor" | "pi" => 10_000,
        "codex" => 9_000,
        "muse-code" => 16_382,
        _ => DEFAULT_CONTEXT_CHARS,
    }
}

fn prompt(key: &str) -> String {
    folded(CONTEXT, key).expect("context.yaml carries the start frame")
}

fn chars(text: &str) -> isize {
    len(text) as isize
}

/// The pending handoff note as session start shows it (§33): in full
/// while young, one line once stale, nothing once picked up or absent.
pub fn render_note(
    store: &dyn State,
    scope: &str,
    mount_path: &str,
    now: Stamp,
) -> Result<Option<String>, Error> {
    let Some(document) = store.read(scope, HANDOFF_PATH)? else {
        return Ok(None);
    };
    if document.envelope.redacted {
        return Ok(None);
    }
    let Some(baton) = parse_handoff(&document.content) else {
        return Ok(None);
    };
    let Some(tier) = handoff_tier(&baton, now) else {
        return Ok(None);
    };
    let written = baton.written.minutes();
    let argument = baton
        .conversation
        .as_ref()
        .map_or(String::new(), |conversation| {
            format!("conversation=\"{conversation}\"")
        });
    let frame = [
        ("actor", baton.actor.as_str()),
        ("written", written.as_str()),
        ("argument", argument.as_str()),
    ];
    Ok(Some(match tier {
        Tier::Line => {
            let mut variables = vec![("mount", mount_path)];
            variables.extend(frame);
            render(&prompt("start_note_line"), &variables)
        }
        Tier::Full => [
            render(&prompt("start_note"), &frame),
            baton.note.clone(),
            prompt("start_note_end"),
        ]
        .join("\n"),
    }))
}

/// The note (never after a compaction), then "where we left off" under
/// what the budget leaves; `own` is the session's id and the start's
/// source.
pub fn render_start_context(
    history: History,
    scope: &str,
    mount_path: &str,
    own: (&str, &str),
    now: Stamp,
    budget_chars: isize,
) -> Result<String, Error> {
    let (own, source) = own;
    let note = if source == COMPACT_SOURCE {
        None
    } else {
        render_note(history.state, scope, mount_path, now)?
    };
    let remaining = note
        .as_ref()
        .map_or(budget_chars, |note| budget_chars - chars(note) - 2);
    let left_off = render_left_off(history, scope, own, source, remaining.max(1))?;
    Ok(match note {
        None => left_off,
        Some(note) => format!("{note}\n\n{left_off}"),
    })
}

/// The start blocks for a door with no hook (`render_instructions`, §33):
/// the memory section with its index, the inbox, then the note and where
/// we left off at the full budgets, the MCP server's instructions where
/// the session is unknown and none is the own one. Read whole: a door
/// that has the store's records passes them to `render_instructions_with`.
pub fn render_instructions(
    store: &dyn State,
    mounts: &[Mount],
    now: Stamp,
) -> Result<String, Error> {
    render_instructions_with(History::new(store, None), mounts, now)
}

/// `render_instructions` from `history`, light records where it keeps them.
pub fn render_instructions_with(
    history: History,
    mounts: &[Mount],
    now: Stamp,
) -> Result<String, Error> {
    let store = history.state;
    if let Some(profile) = store.profile() {
        return crate::profile::context::render_context(
            store,
            history.records,
            mounts,
            profile.context.max_chars,
            None,
        );
    }
    let template = literal(MEMORY, "system_section").expect("memory.yaml carries the section");
    let mut section = render(&template, &[("index", &render_index(store, mounts)?)]);
    let inbox = mailbox_context(&Mailbox::of_mounts(store, mounts, None))?;
    if !inbox.is_empty() {
        section = format!("{section}\n\n{inbox}");
    }
    let Some(mount) = crate::conversation::sessions::sessions_mount(mounts).map(|at| &mounts[at])
    else {
        return Ok(section);
    };
    let rest = render_start_context(
        history,
        &mount.scope,
        &mount.path,
        ("", "startup"),
        now,
        LEFT_OFF_BUDGET_CHARS,
    )?;
    Ok(format!("{section}\n\n{rest}"))
}

/// What the hook prints under the client's ceiling, less `reserve` kept
/// for the recording notices ahead of it: instructions and index up to
/// three eighths, the note and recent sessions under the rest, the
/// session identity and the inbox at the end. `own` is the session's id
/// and the start's source.
pub fn render_session_start(
    history: History,
    mounts: &[Mount],
    mount: &Mount,
    agent: &str,
    own: (&str, &str),
    now: Stamp,
    reserve: usize,
) -> Result<String, Error> {
    let (store, (session_id, _)) = (history.state, own);
    let mut budget = context_chars(agent) - reserve as isize;
    let identity = block(MESSAGES, "identity").expect("messages.yaml carries the identity line");
    let identity = rstrip(&render(&identity, &[("session", session_id)])).to_string();
    let inbox = mailbox_context(&Mailbox::of_mounts(store, mounts, Some(session_id)))?;
    let tail: Vec<&str> = [identity.as_str(), inbox.as_str()]
        .into_iter()
        .filter(|part| !part.is_empty())
        .collect();
    let tail = tail.join("\n\n");
    budget -= chars(&tail) + 2;
    // Pi's direct MCP tools do not inject the server's instructions.
    let instructions = if agent == "pi" {
        let text = literal(CONTEXT, "start_instructions").expect("context.yaml carries them");
        format!("{text}\n\n")
    } else {
        String::new()
    };
    let index = render_within(
        store,
        mounts,
        (budget * 3).div_euclid(8) - chars(&instructions),
    )?;
    let head = format!("{instructions}{}\n{index}", prompt("start_index"));
    let rest = render_start_context(
        history,
        &mount.scope,
        &mount.path,
        own,
        now,
        (budget - chars(&head) - 2).max(1),
    )?;
    Ok(format!("{head}\n\n{rest}\n\n{tail}"))
}
