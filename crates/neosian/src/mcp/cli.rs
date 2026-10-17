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
use crate::home::{home, project_mounts, user_mount};
use crate::memory::cli::{DSN_ENV, NEEDS_PYTHON};
use crate::memory::dispatch::Memory;
use crate::memory::grammar::{SCOPE_ENV, SUGAR_MOUNT_PATH};
use crate::memory::mounts::{Mount, Refusal};
use crate::record::context::render_instructions;
use crate::repr::repr;
use crate::store::FileStore;
use crate::store::grammar::Actor;
use crate::store::time::Stamp;

pub const DEFAULT_ACTOR: &str = "mcp:stdio";
/// The clients whose SessionStart hook prints the index, the note and
/// "where we left off" (§33): their instructions carry the discipline alone.
const HOOKED_CLIENTS: [&str; 5] = ["claude-code", "codex", "cursor", "muse-code", "pi"];

/// Serve neosian memory to MCP clients on stdio.
#[derive(Args)]
pub struct Mcp {
    /// FileStore root directory (created on start; default: the home,
    /// ~/.neosian or $NEOSIAN_HOME)
    #[arg(long)]
    root: Option<PathBuf>,
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
    /// `install`: the registration is the Python shell's until R3
    #[arg(hide = true, trailing_var_arg = true, allow_hyphen_values = true)]
    install: Vec<String>,
}

/// The mount half of the grammar: the flags, else `NEOSIAN_SCOPE`, else
/// the working directory's layout, a nameless directory degrading to the
/// user mount alone.
fn resolve_mounts(args: &Mcp) -> Result<Vec<Mount>, Refusal> {
    if args.scope.is_some() && !args.mount.is_empty() {
        let sugar = "--scope is the single-mount sugar; use --mount for multi-mount";
        return Err(Refusal::Usage(sugar.to_string()));
    }
    if !args.mount.is_empty() {
        let mounts: Vec<Mount> = args
            .mount
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
    if let Some(scope) = args.scope.as_ref().or(named.as_ref()) {
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
        Err(_) => Ok(vec![user_mount()?]),
    }
}

/// Which start door the client has (§33), from the actor the installer
/// rendered (`mcp:<client>`).
fn hooked(actor: &str) -> bool {
    let client = actor.strip_prefix("mcp:").unwrap_or("");
    HOOKED_CLIENTS.contains(&client)
}

pub fn run(args: &Mcp, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    if let Some(first) = args.install.first() {
        if first == "install" {
            writeln!(
                err,
                "error: `neosian mcp install` is the Python shell's until R3: needs the Python shell: `uv tool install neosian`"
            )?;
            return Ok(1);
        }
        let message = format!("unexpected argument {} found", repr(first));
        return usage(false, &message, out, err);
    }
    let mounts = match resolve_mounts(args) {
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
    if env::var_os(DSN_ENV).is_some_and(|dsn| !dsn.is_empty()) {
        writeln!(err, "error: {NEEDS_PYTHON}")?;
        return Ok(1);
    }
    let root = match &args.root {
        Some(root) => root.clone(),
        None => home()?,
    };
    let store = FileStore::open(&root)?;
    let instructions = if hooked(actor.as_str()) {
        literal(CONTEXT, "start_instructions").expect("context.yaml carries the discipline")
    } else {
        match render_instructions(&store, &mounts, Stamp::now()) {
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
