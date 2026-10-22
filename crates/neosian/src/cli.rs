//! The argv grammar (DESIGN §14.1): stdout carries the artifact, stderr the
//! guidance; a bad invocation exits 2, the operation's own failure 1.

use std::ffi::OsString;
use std::io::{self, Write};
use std::process::ExitCode;

use clap::{Parser, Subcommand};
use serde_json::json;

use crate::conversation::{cli_continue, cli_search};
use crate::{clients, configure, docs, mcp, memory, record, serve, version};

#[derive(Parser)]
#[command(
    name = "neosian",
    version = version::VERSION,
    about = "The state layer for LLM agents",
    disable_help_subcommand = true
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Wire the installed agents to this store: MCP and the hooks.
    Setup(clients::setup::Setup),
    /// Is this machine set up? The home, the keys, the clients.
    Status(clients::status::Status),
    /// Store a provider's API key under the home, or list them.
    Configure(configure::Configure),
    /// Display version information.
    Version,
    /// Print a shipped docs page, or list the topics.
    Docs {
        /// Topic to print (omit to list the topics)
        topic: Option<String>,
        /// One JSON object on stdout
        #[arg(long = "json")]
        json: bool,
    },
    /// Read and write agent memory from the shell.
    Memory {
        #[command(subcommand)]
        command: memory::grammar::Command,
    },
    /// Find the turns holding every term, newest first (DESIGN §32).
    Search(cli_search::Search),
    /// Print a recorded conversation as continue_session delivers it (§33).
    Continue(cli_continue::Continue),
    /// Record a foreign agent's session from its hooks: one payload on stdin per call (§20.9).
    Record(record::cli::Record),
    /// Serve neosian memory to MCP clients on stdio (§39.3).
    Mcp(mcp::cli::Mcp),
    /// Serve neosian memory and conversations over HTTP: the state process (§18).
    Serve(serve::cli::Serve),
}

/// A grammar-tier refusal (exit 2): the message on stderr, and where
/// `--json` asked, one `{"error": "usage", "hint"}` object on stdout.
pub fn usage(
    json: bool,
    message: &str,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<u8> {
    if json {
        writeln!(out, "{}", json!({"error": "usage", "hint": message}))?;
    }
    writeln!(err, "error: {message}")?;
    Ok(2)
}

/// Parse and run; the exit code is the verb's.
pub fn run<I>(args: I) -> ExitCode
where
    I: IntoIterator<Item = OsString>,
{
    let args: Vec<OsString> = args.into_iter().collect();
    let stdout = io::stdout();
    let stderr = io::stderr();
    let (mut out, mut err) = (stdout.lock(), stderr.lock());
    let cli = match Cli::try_parse_from(&args) {
        Ok(cli) => cli,
        Err(error) => {
            // The grammar's own text stays on stderr; a caller that asked
            // for JSON also reads the refusal as JSON (DESIGN §30.1).
            if error.use_stderr() && args.iter().any(|arg| arg == "--json") {
                let text = error.to_string();
                let hint = text.lines().next().unwrap_or_default();
                let hint = hint.strip_prefix("error: ").unwrap_or(hint);
                let _ = writeln!(out, "{}", json!({"error": "usage", "hint": hint}));
            }
            error.exit()
        }
    };
    let code = match cli.command {
        Command::Setup(args) => clients::setup::run(&args, &mut out, &mut err),
        Command::Status(args) => clients::status::run(&args, &mut out, &mut err),
        Command::Configure(args) => configure::run(&args, &mut out, &mut err),
        Command::Version => writeln!(out, "{}", version::line()).map(|()| 0),
        Command::Docs { topic, json } => docs::run(topic.as_deref(), json, &mut out, &mut err),
        Command::Memory { command } => memory::cli::run(&command, &mut out, &mut err),
        Command::Search(args) => cli_search::run(&args, &mut out, &mut err),
        Command::Continue(args) => cli_continue::run(&args, &mut out, &mut err),
        Command::Record(args) => record::cli::run(&args, &mut out, &mut err),
        Command::Mcp(args) => mcp::cli::run(&args, &mut out, &mut err),
        Command::Serve(args) => serve::cli::run(&args, &mut out, &mut err),
    };
    match code {
        Ok(code) => ExitCode::from(code),
        // A closed pipe is the reader's choice, not an error of ours.
        Err(error) if error.kind() == io::ErrorKind::BrokenPipe => ExitCode::SUCCESS,
        Err(error) => {
            let _ = writeln!(err, "error: {error}");
            ExitCode::from(1)
        }
    }
}
