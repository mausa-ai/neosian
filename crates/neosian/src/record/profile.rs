//! Profile recording: a private capture destination and a fenced pending span.

use super::cli::Record;
use super::output::{Envelope, respond};
use super::payload::{Payload, parse_payload};
use crate::conversation::{Error, failed};
use crate::profile::model::CaptureMode;
use crate::profile::resolve::Resolved;
use crate::remote::selection::Selection;
use crate::store::grammar::Actor;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::io::{self, Read, Write};

fn digest(value: &[u8]) -> String {
    Sha256::digest(value)
        .iter()
        .flat_map(|byte| {
            const HEX: &[u8; 16] = b"0123456789abcdef";
            [
                HEX[(byte >> 4) as usize] as char,
                HEX[(byte & 15) as usize] as char,
            ]
        })
        .collect()
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
    match process(args, selected, payload) {
        Ok(envelope) => respond(&envelope, &args.agent, args.json, out).map(|()| 0),
        Err(error) => failed(&error.text(), None, args.json, out, err),
    }
}

fn process(args: &Record, selected: Resolved, payload: Payload) -> Result<Envelope, Error> {
    let native = payload["session_id"].as_str().unwrap_or("");
    crate::conversation::ids::parse_conversation_id(native)?;
    let key = digest(
        &serde_json::to_vec(&(&selected.installation_id, &args.agent, native))
            .map_err(io::Error::other)?,
    );
    let id = format!("{}{key}", selected.profile.conversation_prefix());
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
    let policy = selected.profile.capture.clone();
    // Public provenance describes the portable policy, never credential-derived data.
    let policy_id = digest(&serde_json::to_vec(&selected.profile).map_err(io::Error::other)?);
    let mut credentials = serde_json::Map::new();
    if mode != CaptureMode::Off {
        for source in &selected.profile.sources {
            if let Some(name) = selected.bindings[source].credential() {
                let secret = std::env::var(name)
                    .ok()
                    .filter(|v| !v.is_empty())
                    .ok_or_else(|| {
                        Error::Value(format!("required credential variable {name} is unset"))
                    })?;
                credentials.insert(name.into(), json!(digest(secret.as_bytes())));
            }
        }
    }
    let fingerprint = digest(
        &serde_json::to_vec(&json!({"profile":selected.profile,"bindings":selected.bindings,"credentials":credentials}))
            .map_err(io::Error::other)?,
    );
    let history_scope = selected.profile.history.as_ref().map(|h| h.scope.clone());
    let workspace = selected.profile.workspace_id.clone();
    let selected = Selection::Profile(Box::new(selected));
    let mounts = selected
        .mounts()
        .expect("profile selection")
        .map_err(Error::Value)?;
    if event == "SessionStart" {
        let store = selected.open()?;
        let source = payload
            .get("source")
            .and_then(Value::as_str)
            .unwrap_or("startup");
        let mut limit = super::context::context_chars(&args.agent) as usize;
        if args.agent == "muse-code" {
            limit = limit.min(4090);
        }
        envelope.context = Some(crate::profile::context::render_context(
            store.as_ref(),
            &mounts,
            limit,
            Some((&id, source)),
        )?);
        envelope.disposition = "context";
        return Ok(envelope);
    }
    if ["MailboxPoll", "MailboxDelivery", "MailboxContext"].contains(&event.as_str()) {
        let store = selected.open()?;
        let mailbox =
            crate::messaging::mailbox::Mailbox::of_mounts(store.as_ref(), &mounts, Some(&id))
                .with_actor(envelope.actor.clone());
        if store.profile().is_some_and(|p| p.context.mailbox) {
            match event.as_str() {
                "MailboxPoll" => envelope.messages = Some(super::mailbox::poll(&mailbox)?),
                "MailboxDelivery" => super::mailbox::deliver(&mailbox, &payload)?,
                _ => {
                    let text = crate::messaging::delivery::context(&mailbox)?;
                    let limit = store
                        .profile()
                        .expect("profile")
                        .context
                        .max_chars
                        .min(super::context::context_chars(&args.agent) as usize);
                    envelope.context = Some(if text.chars().count() <= limit {
                        text
                    } else {
                        crate::assets::render(
                            &crate::assets::literal(crate::assets::CONTEXT, "profile_omitted")
                                .expect("profile asset"),
                            &[("count", "1")],
                        )
                    });
                }
            }
        } else {
            envelope.messages = Some(vec![]);
        }
        return Ok(envelope);
    }
    if mode == CaptureMode::Off {
        envelope.disposition = if guard.exists() {
            "capture_off_pending"
        } else {
            "capture_off"
        };
        return Ok(envelope);
    }
    let Some(record) = crate::profile::filter::record(&payload, &policy) else {
        envelope.disposition = "excluded";
        return Ok(envelope);
    };
    let spool = super::spool::Spool::new(&spool_dir);
    let _held = spool.lock(&key)?;
    let old = match std::fs::read_to_string(&guard) {
        Ok(old) => Some(old),
        Err(e) if e.kind() == io::ErrorKind::NotFound => None,
        Err(e) => return Err(e.into()),
    };
    if old.as_deref().is_some_and(|old| old != fingerprint) {
        return Err(Error::Value("pending capture uses a different profile, binding or policy; restore that configuration or explicitly resolve the retained local span".into()));
    }
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
        spool: spool_dir,
        provenance: Some(format!(
            "\n- workspace: {workspace}\n- source_session: {native}\n- capture_policy: {policy_id}\n"
        )),
    };
    let landed = super::land::land(&settings, &spool, &id, &envelope.actor, &event, &record)?;
    envelope.disposition = landed.disposition;
    envelope.conversation_id = landed.conversation_id;
    envelope.turn = landed.turn;
    envelope.document = landed.document;
    envelope.client = landed.client;
    if matches!(landed.disposition, "recorded" | "empty") {
        std::fs::remove_file(guard)?;
    }
    Ok(envelope)
}
