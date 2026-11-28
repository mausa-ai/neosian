//! Profile recording: a private capture destination and a fenced pending span.

use super::cli::Record;
use super::fingerprint::{digest, fingerprint};
use super::landing::changed::{Recorder, guarded};
use super::landing::queue::Queue;
use super::output::Envelope;
use super::payload::{Payload, parse_payload};
use crate::conversation::{Error, failed};
use crate::profile::model::CaptureMode;
use crate::profile::resolve::Resolved;
use crate::remote::selection::Selection;
use crate::state::records::HistoryReader;
use crate::store::grammar::Actor;
use serde_json::Value;
use std::io::{self, Read, Write};
use std::path::Path;

/// Capture is off: whether an open span or a closed turn waits (never sent while off).
fn off(guard: &Path, spool_dir: &Path, id: &str) -> io::Result<&'static str> {
    let waits = guard.try_exists()? || Queue::new(spool_dir).has_pending(id)?;
    Ok(["capture_off", "capture_off_pending"][usize::from(waits)])
}

/// The conversation a profile files a native session under: the
/// workspace prefix and the digest of the installation, agent and native ID
/// (the harness tool derives the same ID to find a child, §40.50).
pub fn conversation_id(prefix: &str, installation: &str, agent: &str, native: &str) -> String {
    let key =
        digest(&serde_json::to_vec(&(installation, agent, native)).expect("strings serialize"));
    format!("{prefix}{key}")
}

pub fn run(
    args: &Record,
    selected: Resolved,
    payload: Option<Payload>,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<u8> {
    let checked = selected
        .conflicts(
            args.root.is_some()
                || args.url.is_some()
                || args.schema.is_some()
                || args.scope.is_some()
                || !args.mount.is_empty(),
        )
        .and_then(|()| selected.check());
    if let Err(message) = checked {
        return failed(&message, None, args.json, out, err);
    }
    if args.spool.is_some() {
        return failed(
            "profile spools are managed under the neosian home; omit --spool",
            None,
            args.json,
            out,
            err,
        );
    }
    let payload = match payload {
        Some(payload) => payload,
        None => {
            let mut input = String::new();
            io::stdin().read_to_string(&mut input)?;
            match parse_payload(
                &input.replace("\r\n", "\n").replace('\r', "\n"),
                &args.agent,
            ) {
                Ok(payload) => payload,
                Err(message) => return failed(&message, None, args.json, out, err),
            }
        }
    };
    let selected = if payload.get("hook_event_name").and_then(Value::as_str) == Some("SessionStart")
        && selected.login.is_some()
        && crate::login::refresh_if_due(&selected.home)
    {
        match crate::profile::resolve::selected(args.profile.as_deref(), &selected.project) {
            Ok(Some(current)) => current,
            _ => {
                return failed(
                    "managed workspace assignment changed",
                    None,
                    args.json,
                    out,
                    err,
                );
            }
        }
    } else {
        selected
    };
    let startup = super::landing::startup::profile(&payload, &selected.home);
    let reserve = startup.as_ref().map_or(0, |startup| startup.reserve());
    let native = payload["session_id"].as_str().unwrap_or("-").to_string();
    let prefix = selected.profile.conversation_prefix();
    let id = conversation_id(&prefix, &selected.installation_id, &args.agent, &native);
    match process(args, selected, payload, reserve) {
        Ok(mut envelope) => {
            super::landing::startup::answer(startup, &mut envelope, &args.agent, args.json, out)
                .map(|()| 0)
        }
        Err(error) => {
            let (hint, text) = (super::landing::held::hint(&error), error.text());
            let failure = (text.as_str(), hint.as_deref());
            let alone = super::landing::startup::alone;
            match alone(
                startup,
                &args.agent,
                (&id, &native),
                args.json,
                failure,
                out,
                err,
            )? {
                Some(code) => Ok(code),
                None => failed(&text, hint.as_deref(), args.json, out, err),
            }
        }
    }
}

fn process(
    args: &Record,
    selected: Resolved,
    payload: Payload,
    reserve: usize,
) -> Result<Envelope, Error> {
    let native = payload["session_id"].as_str().unwrap_or("");
    crate::conversation::ids::parse_conversation_id(native)?;
    let id = conversation_id(
        &selected.profile.conversation_prefix(),
        &selected.installation_id,
        &args.agent,
        native,
    );
    let key = id[selected.profile.conversation_prefix().len()..].to_string();
    let actor = Actor::parse(&format!("{}:{native}", args.agent)).map_err(Error::Value)?;
    let event = payload
        .get("hook_event_name")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let mut envelope = Envelope {
        event: event.clone(),
        session_id: id.clone(),
        actor,
        disposition: "ignored",
        conversation_id: None,
        turn: None,
        document: None,
        client: None,
        context: None,
        messages: None,
    };
    let spool_dir = selected.home.join("profile-spool");
    let guard = spool_dir.join(format!("{key}.policy"));
    let mode = selected.profile.capture.mode;
    let context_event =
        event == "SessionStart" || (args.agent == "pi" && event == "MailboxContext");
    let opencode_frame = args.agent == "opencode" && event == "MailboxContext";
    if opencode_frame && !selected.profile.context.mailbox {
        envelope.context = Some(crate::profile::context::identity(&id));
        return Ok(envelope);
    }
    let mailbox_event = matches!(
        event.as_str(),
        "MailboxPoll" | "MailboxDelivery" | "MailboxContext"
    );
    let spool = super::spool::Spool::new(&spool_dir);
    let prompt_event = event == super::payload::PROMPT_EVENT;
    // The harness child's watcher records this session (X4a).
    if !context_event && !mailbox_event && super::settings::watched(native) {
        envelope.disposition = "watched";
        return Ok(envelope);
    }
    // Pause, resume and the entire landing compete on the existing session lock.
    let _held = if context_event || mailbox_event {
        None
    } else {
        let enabled = mode != CaptureMode::Off;
        // A capture-off installation with no pause creates no spool directory.
        let held = if enabled || super::pause::paused(&spool_dir, &id)? {
            Some(spool.lock(&key)?)
        } else {
            None
        };
        // Read a managed machine's latest opt-out under the same lock as
        // the conversation's resume marker and pending turn.
        let enabled = enabled
            && (selected.login.is_none()
                || !crate::login::local::capture_off(&selected.home).map_err(Error::Value)?);
        if held.is_some()
            && (!prompt_event || super::pause::paused(&spool_dir, &id)?)
            && super::pause::blocks(&spool_dir, &id, &key, &payload, &args.agent, enabled)?
        {
            envelope.disposition = "capture_paused";
            return Ok(envelope);
        }
        if !enabled {
            envelope.disposition = off(&guard, &spool_dir, &id)?;
            return Ok(envelope);
        }
        held
    };
    let managed = selected.login.is_some();
    let policy = selected.profile.capture.clone();
    let provenance = super::fingerprint::provenance(&selected.profile, native)?;
    let fingerprint = fingerprint(&selected)?;
    let history_scope = selected.profile.history.as_ref().map(|h| h.scope.clone());
    let project = selected.project.clone();
    let selected = Selection::Profile(Box::new(selected));
    let mounts = selected
        .mounts()
        .expect("profile selection")
        .map_err(Error::Value)?;
    if context_event {
        let history = HistoryReader::open(&selected)?;
        let (store, records) = (history.state(), history.records()?);
        let source = payload
            .get("source")
            .and_then(Value::as_str)
            .unwrap_or("startup");
        let mut limit = super::context::context_chars(&args.agent) as usize;
        if args.agent == "muse-code" {
            limit = limit.min(4090);
        }
        limit = limit.saturating_sub(reserve);
        envelope.context = Some(crate::profile::context::render_context(
            store.as_ref(),
            records.as_deref(),
            &mounts,
            limit,
            Some((&id, source)),
        )?);
        envelope.disposition = "context";
        super::landing::start::profile(&spool_dir, &id, &event, false);
        return Ok(envelope);
    }
    if mailbox_event {
        let store = selected.open()?;
        let mailbox =
            crate::messaging::mailbox::Mailbox::of_mounts(store.as_ref(), &mounts, Some(&id))
                .with_actor(envelope.actor.clone());
        if store.profile().is_some_and(|p| p.context.mailbox) {
            match event.as_str() {
                "MailboxPoll" => envelope.messages = Some(super::mailbox::poll(&mailbox)?),
                "MailboxDelivery" => super::mailbox::deliver(&mailbox, &payload)?,
                _ => {
                    let inbox = crate::messaging::delivery::context(&mailbox)?;
                    let identity = if opencode_frame {
                        crate::profile::context::identity(&id)
                    } else {
                        String::new()
                    };
                    let frame = |body: &str| {
                        [identity.as_str(), body]
                            .into_iter()
                            .filter(|s| !s.is_empty())
                            .collect::<Vec<_>>()
                            .join("\n\n")
                    };
                    let text = frame(&inbox);
                    let limit = store
                        .profile()
                        .expect("profile")
                        .context
                        .max_chars
                        .min(super::context::context_chars(&args.agent) as usize);
                    let limit = if opencode_frame {
                        crate::profile::context::opencode_hook_limit(
                            store.profile().expect("profile"),
                        )
                    } else {
                        limit
                    };
                    envelope.context = Some(if text.chars().count() <= limit {
                        text
                    } else {
                        frame(&crate::assets::render(
                            &crate::assets::literal(crate::assets::CONTEXT, "profile_omitted")
                                .expect("profile asset"),
                            &[("count", "1")],
                        ))
                    });
                }
            }
        } else {
            envelope.messages = Some(vec![]);
        }
        return Ok(envelope);
    }
    if managed
        && crate::login::local::capture_off(spool_dir.parent().expect("spool under home"))
            .map_err(Error::Value)?
    {
        envelope.disposition = off(&guard, &spool_dir, &id)?;
        return Ok(envelope);
    }
    let record = crate::profile::filter::record(&payload, &policy);
    let parent = super::settings::harness_parent(native);
    let recorder = Recorder {
        agent: &args.agent,
        native,
        parent: parent.as_deref(),
    };
    // #447, ruling D5: spans recorded under another policy are held.
    let records = prompt_event || record.is_some();
    let old = guarded(&guard, &key, &fingerprint, records, &recorder)?;
    // Verify ownership of any pending span before admitting the fresh prompt.
    // A policy that keeps only replies still observes this turn boundary.
    if prompt_event && super::pause::blocks(&spool_dir, &id, &key, &payload, &args.agent, true)? {
        envelope.disposition = "capture_paused";
        return Ok(envelope);
    }
    let Some(record) = record else {
        envelope.disposition = "excluded";
        return Ok(envelope);
    };
    if old.is_none() {
        crate::store::fileio::atomic_write(&guard, &fingerprint, true)?;
    }
    let scope = history_scope.expect("validated capture requires history");
    let settings = super::settings::Settings {
        store: selected,
        mounts: vec![crate::memory::mounts::Mount::new(
            &scope,
            "history",
            (false, false),
            "",
        )?],
        sessions_mount: 0,
        agent: args.agent.clone(),
        spool: spool_dir.clone(),
        provenance: Some(provenance),
        parent,
        project,
        fingerprint: Some(fingerprint),
        guard: Some(guard),
    };
    // At its close the span's guard goes with its pause-fence marker.
    let landed = super::land::land(&settings, &spool, &id, &envelope.actor, &event, &record)?;
    super::landing::start::profile(&spool_dir, &id, &event, landed.lander);
    envelope.disposition = landed.disposition;
    envelope.conversation_id = landed.conversation_id;
    envelope.turn = landed.turn;
    envelope.document = landed.document;
    envelope.client = landed.client;
    Ok(envelope)
}
