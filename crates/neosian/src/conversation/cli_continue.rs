//! `neosian continue` (`conversation/cli_continue.py`, DESIGN §33): what
//! `continue_session` delivers, printed. A read: a terminal is not a
//! session, so the note stays pending and no lineage is written. `--scope`
//! names the scope whose sessions and note to read, this directory's
//! project scope by default. Exit tiers (§14.1): 2 for grammar with
//! nothing constructed; 1 when the store answers with an error or there
//! is nothing to continue; 0 with the text.

use std::env;
use std::io::{self, Write};
use std::path::PathBuf;

use clap::Args;
use serde_json::json;

use super::failed;
use super::handoff::continue_conversation;
use super::ids::check_conversation_id;
use crate::cli::usage;
use crate::home::project_scope;
use crate::memory::grammar::SCOPE_ENV;
use crate::memory::mounts::Refusal;
use crate::remote::selection::Selection;
use crate::repr::repr;
use crate::store::grammar::check_scope;

/// Print a recorded conversation the way continue_session delivers it.
#[derive(Args)]
pub struct Continue {
    /// the conversation to deliver (default: the pending note's session,
    /// else the newest listed in the scope)
    conversation: Option<String>,
    /// FileStore root directory (created on start; default: the home,
    /// ~/.neosian or $NEOSIAN_HOME)
    #[arg(long)]
    root: Option<PathBuf>,
    /// State process URL; token from NEOSIAN_CLIENT_TOKEN.
    #[arg(long)]
    url: Option<String>,
    /// Postgres schema (Postgres only).
    #[arg(long)]
    schema: Option<String>,
    #[arg(long)]
    profile: Option<String>,
    /// the scope whose sessions and note to read (default: $NEOSIAN_SCOPE,
    /// else this directory's project scope)
    #[arg(long)]
    scope: Option<String>,
    /// one JSON object
    #[arg(long)]
    json: bool,
}

pub fn run(args: &Continue, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let json = args.json;
    let selected = match Selection::resolve_profile(
        args.root.as_deref(),
        args.url.as_deref(),
        args.schema.as_deref(),
        args.profile.as_deref(),
        &std::env::current_dir()?,
        args.scope.is_some(),
    ) {
        Ok(selected) => selected,
        Err(message) => return usage(json, &message, out, err),
    };
    let checked = args.conversation.as_deref().map(check_conversation_id);
    if let (Some(Err(reason)), Some(conversation)) = (checked, &args.conversation) {
        let message = format!("conversation {}: {reason}", repr(conversation));
        return usage(json, &message, out, err);
    }
    let scoped = match &selected {
        Selection::Profile(p) => p
            .profile
            .history
            .as_ref()
            .map(|h| h.scope.clone())
            .ok_or_else(|| "this profile has no history selection".to_string()),
        _ => resolve_scope(args.scope.as_deref())?,
    };
    let scope = match scoped {
        Ok(scope) => scope,
        Err(message) => return usage(json, &message, out, err),
    };
    let store = match selected.open() {
        Ok(store) => store,
        Err(error) => return failed(&super::Error::from(error).text(), None, json, out, err),
    };
    let outcome =
        match continue_conversation(store.as_ref(), &scope, args.conversation.as_deref(), None) {
            Ok(Ok(outcome)) => outcome,
            Ok(Err(corrective)) => {
                return failed(&corrective.error, corrective.hint, json, out, err);
            }
            Err(error) => return failed(&error.text(), None, json, out, err),
        };
    if json {
        let envelope = json!({
            "conversation": outcome.conversation,
            "scope": scope,
            "note": outcome.note,
            "client": store.client(),
            "text": outcome.text,
        });
        writeln!(out, "{envelope}")?;
    } else {
        writeln!(out, "{}", outcome.text)?;
    }
    Ok(0)
}

/// The flag, else `NEOSIAN_SCOPE`, else the working directory's project
/// scope: the audit verb's rule, the mount the sessions land in. The
/// refusal is the grammar's sentence (exit 2).
fn resolve_scope(flag: Option<&str>) -> io::Result<Result<String, String>> {
    let named = env::var(SCOPE_ENV).ok().filter(|scope| !scope.is_empty());
    let Some(scope) = flag.map(str::to_string).or(named) else {
        return Ok(
            project_scope(&env::current_dir()?).map_err(|refusal| match refusal {
                Refusal::Usage(message) => message,
                Refusal::Memory(error) => error.message,
            }),
        );
    };
    Ok(check_scope(&scope)
        .map(|()| scope)
        .map_err(|reason| format!("--scope: {reason}")))
}
