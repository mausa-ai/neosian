//! The verb's grammar and dispatch (`record/cli.py`, `record/settings.py`,
//! DESIGN §20.9): the store half and the mount half are the memory
//! grammar's own, new here are the foreign agent's kind and the spool, and
//! no `--actor`: the writer is `<agent>:<session_id>` by construction.
//! Exit tiers under a hook's semantics (Claude Code reads 2 as "block"):
//! 2 only for argv, nothing constructed, and 1 for everything after it (a
//! login the environment does not name, bad stdin, an unreachable store, a
//! corrupt spool), so a broken store never blocks the agent. Registered
//! once per machine (§22.6), the verb derives the layout per session from
//! `--project` (else the working directory), and a directory with no name
//! records to the user mount alone.

use std::env;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};

use super::context::render_session_start;
use super::land::land;
use super::mailbox::{deliver, note_continue, poll};
use super::output::{Envelope, respond};
use super::payload::{
    PROMPT_EVENT, Payload, SESSION_START_EVENT, TOOL_EVENT, parse_payload, reduce_payload,
};
use super::settings::{Settings, cursor_project, environment, layout_of, resolve_mounts};
use super::spool::Spool;
use crate::cli::usage;
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::sessions::sessions_mount;
use crate::conversation::{Error, failed};
use crate::home::home;
use crate::memory::grammar::SCOPE_ENV;
use crate::memory::mounts::{Mount, Refusal};
use crate::messaging::delivery::context as mailbox_context;
use crate::messaging::mailbox::Mailbox;
use crate::pyfmt::py_str;
use crate::remote::selection::Selection;
use crate::repr::repr;
use crate::store::grammar::Actor;
use crate::store::time::Stamp;
use clap::Args;

const DEFAULT_AGENT: &str = "claude-code";
const SPOOL_DIR_NAME: &str = "spool";
pub const HINT: &str = "the record never blocks the agent — fix the cause and the next Stop lands \
                        the whole span (the spool keeps it)";
const CURSOR_HINT: &str = "hint: Cursor has no unambiguous project; using /user. Set --project, \
                           --scope or --mount to select one.";

/// Record a foreign agent's session from its hooks: one payload on stdin per call.
#[derive(Args)]
pub struct Record {
    /// Select a portable workspace profile and its local capture policy.
    #[arg(long)]
    pub(super) profile: Option<String>,
    #[command(subcommand)]
    command: Option<crate::clients::install::RecordCommand>,
    /// FileStore root directory (created on start; default: the home,
    /// ~/.neosian or $NEOSIAN_HOME)
    #[arg(long)]
    pub(super) root: Option<PathBuf>,
    /// State process URL; bearer token comes from NEOSIAN_CLIENT_TOKEN.
    #[arg(long)]
    pub(super) url: Option<String>,
    /// Postgres schema (Postgres only).
    #[arg(long)]
    pub(super) schema: Option<String>,
    /// single read-write mount of SCOPE at /memories (the memory_scope= sugar)
    #[arg(long)]
    pub(super) scope: Option<String>,
    /// explicit mount; repeatable for multi-mount setups (ro = read-only,
    /// eo = edit-only: existing documents only)
    #[arg(long, value_name = "scope=...,path=...[,ro|,eo]")]
    pub(super) mount: Vec<String>,
    /// the foreign agent's actor kind: the writer is <agent>:<session_id>
    /// (default: claude-code)
    #[arg(long, default_value = DEFAULT_AGENT)]
    pub(super) agent: String,
    /// where an open span waits between the prompt and the stop; never the
    /// store (default: spool/ under the home)
    #[arg(long)]
    pub(super) spool: Option<PathBuf>,
    /// one diagnostic JSON envelope on stdout (otherwise the client's hook
    /// response, including startup context)
    #[arg(long)]
    pub(super) json: bool,
    /// the directory whose layout is the default mounts (default: the
    /// working directory, which a client's `cd` moves)
    #[arg(long)]
    pub(super) project: Option<PathBuf>,
}

pub fn run(args: &Record, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    if let Some(crate::clients::install::RecordCommand::Install(install)) = &args.command {
        if args.profile.is_some() {
            return usage(args.json, "place --profile after install", out, err);
        }
        return crate::clients::install::record(install, out, err);
    }
    let json = args.json;
    let cwd = env::current_dir()?;
    let layout = layout_of(args.project.as_deref(), &cwd);
    if args.agent != "cursor"
        || args.project.is_some()
        || args.profile.is_some()
        || env::var_os("NEOSIAN_PROFILE").is_some_and(|v| !v.is_empty())
    {
        match crate::profile::resolve::selected(args.profile.as_deref(), &layout) {
            Ok(Some(selected)) => return super::profile::run(args, selected, None, out, err),
            Ok(None) => {}
            Err(message) => return failed(&message, None, json, out, err),
        }
    }
    let mut settled = |layout: &Path| -> io::Result<Result<Result<Vec<Mount>, String>, u8>> {
        Ok(match resolve_mounts(args, layout) {
            Ok(resolved) => Ok(resolved),
            Err(Refusal::Usage(message)) => Err(usage(json, &message, out, err)?),
            Err(Refusal::Memory(error)) => {
                writeln!(err, "error: [{}] {}", error.code, error.message)?;
                Err(2)
            }
        })
    };
    let mut mounts = match settled(&layout_of(args.project.as_deref(), &cwd))? {
        Ok(Ok(mounts)) => mounts,
        Ok(Err(message)) => return environment(&message, err),
        Err(code) => return Ok(code),
    };
    let Some(mut sessions) = sessions_mount(&mounts) else {
        let needs = "the sessions document needs a read-write mount (no ,ro or ,eo)";
        return usage(json, needs, out, err);
    };
    if let Err(reason) = Actor::parse(&format!("{}:session", args.agent)) {
        let message = format!(
            "--agent {}: {reason} (a kind, e.g. claude-code)",
            repr(&args.agent)
        );
        return usage(json, &message, out, err);
    }
    let store = match Selection::resolve_schema(
        args.root.as_deref(),
        args.url.as_deref(),
        args.schema.as_deref(),
    ) {
        Ok(store) => store,
        Err(message) => return usage(json, &message, out, err),
    };

    let spool = match &args.spool {
        Some(spool) => spool.clone(),
        None => home()?.join(SPOOL_DIR_NAME),
    };
    let mut text = String::new();
    if let Err(error) = io::stdin().read_to_string(&mut text) {
        return failed(&error.to_string(), Some(HINT), json, out, err);
    }
    if cfg!(windows) {
        text = text.replace("\r\n", "\n").replace('\r', "\n");
    }
    let payload = match parse_payload(&text, &args.agent) {
        Ok(payload) => payload,
        Err(message) => return failed(&message, Some(HINT), json, out, err),
    };
    if args.agent == "cursor" && args.project.is_none() {
        let project = cursor_project(&payload);
        match crate::profile::resolve::selected(args.profile.as_deref(), &project) {
            Ok(Some(selected)) => {
                return super::profile::run(args, selected, Some(payload), out, err);
            }
            Ok(None) => {}
            Err(message) => return failed(&message, None, json, out, err),
        }
        mounts = match settled(&project)? {
            Ok(Ok(mounts)) => mounts,
            Ok(Err(message)) => return failed(&message, Some(HINT), json, out, err),
            Err(code) => return Ok(code),
        };
        let Some(found) = sessions_mount(&mounts) else {
            let needs = "the sessions document needs a read-write mount (no ,ro or ,eo)";
            return failed(needs, Some(HINT), json, out, err);
        };
        sessions = found;
        let named = env::var(SCOPE_ENV).ok().filter(|scope| !scope.is_empty());
        if project == Path::new("/")
            && args.scope.is_none()
            && args.mount.is_empty()
            && named.is_none()
        {
            writeln!(err, "{CURSOR_HINT}")?;
        }
    }
    let settings = Settings {
        provenance: None,
        store,
        mounts,
        sessions_mount: sessions,
        agent: args.agent.clone(),
        spool,
    };
    match record_payload(&settings, &payload, err) {
        Ok(envelope) => respond(&envelope, &settings.agent, json, out).map(|()| 0),
        Err(error) => failed(&error.text(), Some(HINT), json, out, err),
    }
}

/// One payload in, the envelope out.
fn record_payload(
    settings: &Settings,
    payload: &Payload,
    err: &mut impl Write,
) -> Result<Envelope, Error> {
    let session_id = payload["session_id"].as_str().unwrap_or_default();
    parse_conversation_id(session_id)?;
    let actor =
        Actor::parse(&format!("{}:{session_id}", settings.agent)).map_err(io::Error::other)?;
    let event = payload
        .get("hook_event_name")
        .map_or_else(|| "None".to_string(), py_str);
    let (disposition, record) = reduce_payload(payload);
    let mut envelope = Envelope {
        event,
        session_id: session_id.to_string(),
        actor,
        disposition,
        conversation_id: None,
        turn: None,
        document: None,
        context: None,
        messages: None,
        client: None,
    };
    let event = envelope.event.as_str();
    if matches!(event, "MailboxPoll" | "MailboxDelivery") {
        let store = settings.store.open()?;
        let mailbox = Mailbox::of_mounts(store.as_ref(), &settings.mounts, Some(session_id))
            .with_actor(envelope.actor.clone());
        envelope.messages = Some(if event == "MailboxPoll" {
            poll(&mailbox)?
        } else {
            deliver(&mailbox, payload)?;
            Vec::new()
        });
        return Ok(envelope);
    }
    let mount = &settings.mounts[settings.sessions_mount];
    if event == "MailboxContext" {
        let store = settings.store.open()?;
        let mailbox = Mailbox::of_mounts(store.as_ref(), &settings.mounts, Some(session_id));
        let identity = crate::assets::block(crate::assets::MESSAGES, "identity")
            .expect("messages.yaml carries the identity line");
        let identity = crate::text::rstrip(&crate::assets::render(
            &identity,
            &[("session", session_id)],
        ))
        .to_string();
        let inbox = mailbox_context(&mailbox)?;
        let parts: Vec<&str> = [identity.as_str(), inbox.as_str()]
            .into_iter()
            .filter(|part| !part.is_empty())
            .collect();
        envelope.context = Some(parts.join("\n\n"));
        return Ok(envelope);
    }
    if event == SESSION_START_EVENT {
        let store = settings.store.open()?;
        envelope.client = store.client().map(str::to_string);
        let source = payload
            .get("source")
            .filter(|s| !crate::conversation::message::falsy(s));
        let source = source.map_or_else(String::new, py_str);
        envelope.disposition = "context";
        envelope.context = Some(render_session_start(
            store.as_ref(),
            &settings.mounts,
            mount,
            &settings.agent,
            session_id,
            &source,
            Stamp::now(),
        )?);
        return Ok(envelope);
    }
    let Some(record) = record else {
        return Ok(envelope);
    };
    let spool = Spool::new(&settings.spool);
    let landed = {
        let _held = spool.lock(session_id)?;
        land(
            settings,
            &spool,
            session_id,
            &envelope.actor,
            event,
            &record,
        )?
    };
    envelope.disposition = landed.disposition;
    envelope.conversation_id = landed.conversation_id;
    envelope.turn = landed.turn;
    envelope.document = landed.document;
    envelope.client = landed.client;
    if matches!(event, PROMPT_EVENT | TOOL_EVENT) {
        // The lineage note a continue call leaves, then the inbox (§34).
        let inbox = settings
            .store
            .open()
            .map_err(Error::from)
            .and_then(|store| {
                let mailbox =
                    Mailbox::of_mounts(store.as_ref(), &settings.mounts, Some(session_id))
                        .with_actor(envelope.actor.clone());
                if event == TOOL_EVENT {
                    note_continue(&mailbox, &mount.scope, payload)?;
                }
                mailbox_context(&mailbox)
            });
        match inbox {
            Ok(inbox) if settings.agent == "claude-code" => {
                envelope.context = Some(inbox).filter(|inbox| !inbox.is_empty());
            }
            Ok(_) => {}
            Err(_) => writeln!(err, "mailbox unavailable; recording remains spooled")?,
        }
    }
    Ok(envelope)
}
