//! The mailbox CLI shares its command service with MCP and its flags with memory.

use std::env;
use std::io::{self, Write};

use clap::Args;
use serde_json::json;

use super::dispatch::{COMMANDS, DELIVERIES, STATUSES, dispatch};
use crate::cli::usage;
use crate::conversation::Error;
use crate::memory::cli::render;
use crate::memory::dispatch::{Memory, Outcome};
use crate::memory::grammar::Flags;
use crate::memory::mounts::Refusal;

/// Durable messages and reminders.
#[derive(Args)]
pub struct Messages {
    #[arg(value_parser = COMMANDS)]
    command: String,
    #[command(flatten)]
    flags: Flags,
    /// Destination scope or mount (default: project, then the first mount).
    #[arg(long)]
    target: Option<String>,
    #[arg(long)]
    session: Option<String>,
    #[arg(long)]
    message_id: Option<String>,
    #[arg(long, allow_hyphen_values = true)]
    body: Option<String>,
    #[arg(long)]
    conversation: Option<String>,
    #[arg(long)]
    due_at: Option<String>,
    #[arg(long)]
    about: Option<String>,
    #[arg(long)]
    token: Option<String>,
    #[arg(long, allow_hyphen_values = true)]
    outcome: Option<String>,
    #[arg(long, allow_negative_numbers = true)]
    turn: Option<i64>,
    #[arg(long, allow_negative_numbers = true)]
    delay_seconds: Option<i64>,
    #[arg(long, allow_negative_numbers = true)]
    about_turn: Option<i64>,
    /// Mounted document to reference; its current version is captured, never its body.
    #[arg(long)]
    context: Option<String>,
    #[arg(long)]
    context_version: Option<u64>,
    #[arg(long)]
    change_id: Option<String>,
    #[arg(long, allow_negative_numbers = true)]
    occurrence: Option<i64>,
    #[arg(long, value_parser = DELIVERIES, default_value = "next_activity")]
    delivery: String,
    #[arg(long, value_parser = STATUSES, default_value = "unread")]
    status: String,
    #[arg(long, default_value_t = 50, allow_negative_numbers = true)]
    limit: i64,
    #[arg(long)]
    actionable: bool,
}

pub fn run(args: &Messages, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let flags = &args.flags;
    let cwd = env::current_dir()?;
    let selected = match flags.select(&cwd) {
        Ok(selected) => selected,
        Err(message) => return usage(flags.json, &message, out, err),
    };
    let (mounts, actor) = match flags.selected_settings(&cwd, &selected) {
        Ok(settings) => settings,
        Err(Refusal::Usage(message)) => return usage(flags.json, &message, out, err),
        Err(Refusal::Memory(error)) => {
            writeln!(err, "error: [{}] {}", error.code, error.message)?;
            return Ok(2);
        }
    };
    let target = args.target.as_deref().or_else(|| {
        (args.command != "list").then(|| {
            mounts
                .iter()
                .find(|mount| mount.path == "project")
                .unwrap_or(&mounts[0])
                .scope
                .as_str()
        })
    });
    let arguments = json!({
        "command": args.command,
        "scope": target,
        "session": args.session,
        "message_id": args.message_id,
        "body": args.body,
        "conversation": args.conversation,
        "turn": args.turn,
        "delivery": args.delivery,
        "actionable": args.actionable,
        "due_at": args.due_at,
        "delay_seconds": args.delay_seconds,
        "about": args.about,
        "about_turn": args.about_turn,
        "context": args.context,
        "context_version": args.context_version,
        "change_id": args.change_id,
        "occurrence": args.occurrence,
        "token": args.token,
        "outcome": args.outcome,
        "status": args.status,
        "limit": args.limit,
    });
    let store = match selected.open() {
        Ok(store) => store,
        Err(error) => {
            return render(
                &Outcome::fail(Error::from(error).text()),
                flags.json,
                out,
                err,
            );
        }
    };
    let memory = Memory {
        store,
        mounts,
        actor,
    };
    render(
        &dispatch(&memory, arguments.as_object().expect("argument object")),
        flags.json,
        out,
        err,
    )
}
