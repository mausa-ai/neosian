//! `neosian mcp` (`neosian/mcp/serve.py`, `mcp/settings.py`, DESIGN §14.5,
//! §22.6): the store flags are the memory grammar's own with `mcp:stdio`
//! as the actor; a client spawns the server wherever its session runs, so
//! a directory with no name serves the user mount, never a refusal. The
//! start door is read from the actor the installer rendered (§33): a
//! hooked client's instructions carry the write discipline alone, any
//! other client gets the whole block. stdout is the wire.

use std::env;
use std::io::{self, Write};
use std::path::PathBuf;

use clap::Args;

use super::rpc::serve;
use super::server::Server;
use crate::assets::{CONTEXT, literal};
use crate::cli::usage;
use crate::home::{project_mounts, user_mount};
use crate::memory::dispatch::Memory;
use crate::memory::grammar::{SCOPE_ENV, SUGAR_MOUNT_PATH};
use crate::memory::mounts::{Mount, Refusal};
use crate::record::context::render_instructions;
use crate::remote::selection::Selection;
use crate::repr::repr;
use crate::store::grammar::Actor;
use crate::store::time::Stamp;

pub const DEFAULT_ACTOR: &str = "mcp:stdio";
/// The clients whose SessionStart hook prints the index, the note and
/// "where we left off" (§33): their instructions carry the discipline alone.
const HOOKED_CLIENTS: [&str; 5] = ["claude-code", "codex", "cursor", "muse-code", "pi"];

/// Serve neosian memory to MCP clients on stdio.
#[derive(Args)]
pub struct Mcp {
    /// Select a portable workspace profile and its local bindings.
    #[arg(long)]
    profile: Option<String>,
    /// FileStore root directory (created on start; default: the home,
    /// ~/.neosian or $NEOSIAN_HOME)
    #[arg(long)]
    root: Option<PathBuf>,
    /// State process URL; bearer token comes from NEOSIAN_CLIENT_TOKEN.
    #[arg(long)]
    url: Option<String>,
    /// Postgres schema (Postgres only).
    #[arg(long)]
    schema: Option<String>,
    /// single read-write mount of SCOPE at /memories
    #[arg(long)]
    scope: Option<String>,
    /// explicit mount; repeatable (ro = read-only, eo = edit-only:
    /// existing documents only)
    #[arg(long, value_name = "scope=...,path=...[,ro|,eo]")]
    mount: Vec<String>,
    /// who writes: <kind>:<id>[/...], recorded on every row
    #[arg(long, default_value = DEFAULT_ACTOR)]
    actor: String,
    #[command(subcommand)]
    command: Option<crate::clients::install::McpCommand>,
}

/// The mount half of the shared grammar (`resolve_mounts`): the flags,
/// else `NEOSIAN_SCOPE`, else the working directory's layout; `nameless`
/// is what a directory with no project name yields (§22.6: the user mount
/// for a door a client spawns, nothing for the state process).
pub fn resolve_mounts(
    scope: Option<&str>,
    mount: &[String],
    nameless: impl FnOnce() -> Result<Vec<Mount>, Refusal>,
) -> Result<Vec<Mount>, Refusal> {
    if scope.is_some() && !mount.is_empty() {
        let sugar = "--scope is the single-mount sugar; use --mount for multi-mount";
        return Err(Refusal::Usage(sugar.to_string()));
    }
    if !mount.is_empty() {
        let mounts: Vec<Mount> = mount
            .iter()
            .map(|token| Mount::parse(token))
            .collect::<Result<_, _>>()?;
        let twice = |mount: &Mount| {
            mounts
                .iter()
                .filter(|other| other.path == mount.path)
                .count()
                > 1
        };
        if mounts.iter().any(twice) {
            return Err(Refusal::Usage("mount paths must be unique".to_string()));
        }
        return Ok(mounts);
    }
    let named = env::var(SCOPE_ENV).ok().filter(|scope| !scope.is_empty());
    if let Some(scope) = scope.or(named.as_deref()) {
        return Ok(vec![Mount::new(
            scope,
            SUGAR_MOUNT_PATH,
            (false, false),
            "",
        )?]);
    }
    let cwd = env::current_dir().map_err(|error| Refusal::Usage(error.to_string()))?;
    match project_mounts(&cwd) {
        Ok(mounts) => Ok(mounts),
        Err(_) => nameless(),
    }
}

/// Which start door the client has (§33), from the actor the installer
/// rendered (`mcp:<client>`).
fn hooked(actor: &str) -> bool {
    let client = actor.strip_prefix("mcp:").unwrap_or("");
    HOOKED_CLIENTS.contains(&client)
}

pub fn run(args: &Mcp, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    if let Some(crate::clients::install::McpCommand::Install(install)) = &args.command {
        if args.profile.is_some() {
            return usage(false, "place --profile after install", out, err);
        }
        return crate::clients::install::mcp(install, out, err);
    }
    let selected = match Selection::resolve_profile(
        args.root.as_deref(),
        args.url.as_deref(),
        args.schema.as_deref(),
        args.profile.as_deref(),
        &env::current_dir()?,
        args.scope.is_some() || !args.mount.is_empty(),
    ) {
        Ok(selected) => selected,
        Err(message) => return usage(false, &message, out, err),
    };
    let resolved_mounts = selected
        .mounts()
        .map(|m| m.map_err(Refusal::Usage))
        .unwrap_or_else(|| {
            resolve_mounts(args.scope.as_deref(), &args.mount, || {
                Ok(vec![user_mount()?])
            })
        });
    let mounts = match resolved_mounts {
        Ok(mounts) => mounts,
        Err(Refusal::Usage(message)) => return usage(false, &message, out, err),
        Err(Refusal::Memory(error)) => {
            writeln!(err, "error: [{}] {}", error.code, error.message)?;
            return Ok(2);
        }
    };
    let actor = match Actor::parse(&args.actor) {
        Ok(actor) => actor,
        Err(reason) => {
            let message = format!(
                "--actor {}: {reason} (e.g. cli:claude-code)",
                repr(&args.actor)
            );
            return usage(false, &message, out, err);
        }
    };
    let store = match selected.open() {
        Ok(store) => store,
        Err(error) => {
            writeln!(
                err,
                "error: {}",
                crate::conversation::Error::from(error).text()
            )?;
            return Ok(1);
        }
    };
    let instructions = if hooked(actor.as_str()) {
        literal(
            CONTEXT,
            if store.profile().is_some() {
                "profile_instructions"
            } else {
                "start_instructions"
            },
        )
        .expect("context.yaml carries the discipline")
    } else {
        match render_instructions(store.as_ref(), &mounts, Stamp::now()) {
            Ok(instructions) => instructions,
            Err(error) => {
                writeln!(err, "error: {}", error.text())?;
                return Ok(1);
            }
        }
    };
    let memory = Memory {
        store,
        mounts,
        actor,
    };
    let mut server = Server::new(memory, instructions);
    serve(&mut server, io::stdin().lock(), out)?;
    Ok(0)
}
