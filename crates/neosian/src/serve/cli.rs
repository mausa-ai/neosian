//! `neosian serve` (`neosian/server/serve.py`, `_foundation/server/settings.py`,
//! DESIGN §18.7): the store flags are the shared grammar's with `serve:mcp`
//! as the actor, the mounts default to the working directory's layout and a
//! nameless directory yields none (§18's relaxation); the token arrives
//! only through `NEOSIAN_SERVE_TOKEN`, and an unset one refuses to start at
//! the grammar tier. Exit tiers (§14.1): 2 for grammar with nothing built,
//! 1 when the bind or the store fails, 0 after a graceful stop.

use std::env;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use clap::Args;
use tokio::sync::watch;

use super::{Shared, auth, mcp, routes};
use crate::cli::usage;
use crate::home::home;
use crate::mcp::cli::resolve_mounts;
use crate::mcp::server::Server;
use crate::memory::cli::{DSN_ENV, NEEDS_PYTHON};
use crate::memory::dispatch::Memory;
use crate::memory::mounts::{Mount, Refusal};
use crate::record::context::render_instructions;
use crate::repr::repr;
use crate::store::FileStore;
use crate::store::grammar::Actor;
use crate::store::time::Stamp;

/// The `/mcp` surface's process-wide actor (DESIGN §18.6, §20).
pub const DEFAULT_ACTOR: &str = "serve:mcp";
pub const TOKEN_ENV: &str = "NEOSIAN_SERVE_TOKEN";
const CLIENT_TOKEN_ENV: &str = "NEOSIAN_CLIENT_TOKEN";
const DEFAULT_HOST: &str = "127.0.0.1";
/// "NEOS" on a phone keypad; unassigned in the registered range.
const DEFAULT_PORT: i64 = 6367;

/// Serve neosian memory and conversations over HTTP: the state process.
#[derive(Args)]
pub struct Serve {
    /// FileStore root directory (created on start; default: the home,
    /// ~/.neosian or $NEOSIAN_HOME)
    #[arg(long)]
    root: Option<PathBuf>,
    /// the state process to speak to: a client's flag, refused here
    #[arg(long)]
    url: Option<String>,
    /// Postgres schema (Postgres only)
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
    /// bind address (default 127.0.0.1)
    #[arg(long, default_value = DEFAULT_HOST)]
    host: String,
    /// port (default 6367)
    #[arg(long, default_value_t = DEFAULT_PORT, allow_negative_numbers = true)]
    port: i64,
}

fn set(name: &str) -> Option<String> {
    env::var(name).ok().filter(|value| !value.is_empty())
}

fn refuse(message: impl Into<String>) -> Refusal {
    Refusal::Usage(message.into())
}

/// The grammar tier (`parse_args`): what is refused at exit 2 with nothing
/// built, in the Python door's order and sentences; the mounts resolved
/// are the `/mcp` surface's, none meaning no surface (§18's relaxation).
fn grammar(args: &Serve) -> Result<(auth::Clients, Vec<Mount>, Actor), Refusal> {
    if !(1..=65535).contains(&args.port) {
        return Err(refuse(format!(
            "--port must be in 1..65535, got {}",
            args.port
        )));
    }
    let dsn = set(DSN_ENV);
    let named: Vec<&str> = [
        ("--root", args.root.is_some()),
        (DSN_ENV, dsn.is_some()),
        ("--url", args.url.is_some()),
    ]
    .into_iter()
    .filter_map(|(name, present)| present.then_some(name))
    .collect();
    if named.len() > 1 {
        return Err(refuse(format!(
            "{} are mutually exclusive",
            named.join(" and ")
        )));
    }
    if args.schema.is_some() && dsn.is_none() {
        return Err(refuse(
            "--schema applies only to Postgres (unset --root/--url)",
        ));
    }
    if let Some(url) = &args.url {
        if !url.starts_with("http://") && !url.starts_with("https://") {
            return Err(refuse(format!(
                "--url must start with http:// or https://, got {}",
                repr(url)
            )));
        }
        if set(CLIENT_TOKEN_ENV).is_none() {
            return Err(refuse(format!(
                "--url needs {CLIENT_TOKEN_ENV} — the client's bearer token, never on the \
                 command line"
            )));
        }
        return Err(refuse(
            "--url is a client's flag: the state process serves a root or a DSN",
        ));
    }
    let mounts = resolve_mounts(args.scope.as_deref(), &args.mount, || Ok(Vec::new()))?;
    let Some(token) = set(TOKEN_ENV) else {
        return Err(refuse(format!(
            "{TOKEN_ENV} is required — the state process never serves unauthenticated"
        )));
    };
    let clients = auth::parse_clients(&token).map_err(refuse)?;
    let actor = Actor::parse(&args.actor).map_err(|reason| {
        refuse(format!(
            "--actor {}: {reason} (e.g. cli:claude-code)",
            repr(&args.actor)
        ))
    })?;
    Ok((clients, mounts, actor))
}

/// The `/mcp` surface (`build_app` with mounts, §18.6): the same server the
/// stdio door serves, its instructions rendered once per process (#51 on
/// this transport), writing as the process-wide actor.
fn surface(
    root: &Path,
    mounts: Vec<Mount>,
    actor: Actor,
) -> io::Result<Result<mcp::Surface, String>> {
    let store = FileStore::open(root)?;
    let instructions = match render_instructions(&store, &mounts, Stamp::now()) {
        Ok(instructions) => instructions,
        Err(error) => return Ok(Err(error.text())),
    };
    let memory = Memory {
        store: std::sync::Arc::new(store),
        mounts,
        actor,
    };
    Ok(Ok(mcp::Surface::new(Server::new(memory, instructions))))
}

/// SIGTERM or an interrupt: the graceful path, in-flight requests drained
/// and every standing `/mcp` stream ended so the connections can close.
async fn shutdown(closing: Option<watch::Sender<bool>>) {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{SignalKind, signal};
        let mut terminate = signal(SignalKind::terminate()).expect("the runtime takes a signal");
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {}
            _ = terminate.recv() => {}
        }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
    if let Some(closing) = closing {
        let _ = closing.send(true);
    }
}

pub fn run(args: &Serve, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let (clients, mounts, actor) = match grammar(args) {
        Ok(parsed) => parsed,
        Err(Refusal::Usage(message)) => return usage(false, &message, out, err),
        Err(Refusal::Memory(error)) => {
            writeln!(err, "error: [{}] {}", error.code, error.message)?;
            return Ok(2);
        }
    };
    if set(DSN_ENV).is_some() {
        writeln!(err, "error: {NEEDS_PYTHON}")?;
        return Ok(1);
    }
    let root = match &args.root {
        Some(root) => root.clone(),
        None => home()?,
    };
    let store = FileStore::open(&root)?;
    let mcp = if mounts.is_empty() {
        None
    } else {
        match surface(&root, mounts, actor)? {
            Ok(surface) => Some(surface),
            Err(text) => {
                writeln!(err, "error: {text}")?;
                return Ok(1);
            }
        }
    };
    let closing = mcp.as_ref().map(|surface| surface.closing.clone());
    let shared = Arc::new(Shared {
        store,
        clients,
        mcp,
    });
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let served: io::Result<()> = runtime.block_on(async {
        let bind = (args.host.as_str(), args.port as u16);
        let listener = tokio::net::TcpListener::bind(bind).await?;
        writeln!(
            err,
            "neosian serve: listening on http://{}",
            listener.local_addr()?
        )?;
        axum::serve(listener, routes::router(shared))
            .with_graceful_shutdown(shutdown(closing))
            .await
    });
    match served {
        Ok(()) => {
            writeln!(err, "Shutting down")?;
            writeln!(err, "Application shutdown complete.")?;
            Ok(0)
        }
        Err(error) => {
            writeln!(err, "error: {error}")?;
            Ok(1)
        }
    }
}
