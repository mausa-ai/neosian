//! The argv grammar (DESIGN §14.1): stdout carries the artifact, stderr the
//! guidance; a bad invocation exits 2, the operation's own failure 1.

use std::ffi::OsString;
use std::io::{self, Write};
use std::process::ExitCode;

use clap::{Parser, Subcommand};

use crate::{docs, version};

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
}

/// Parse and run; the exit code is the verb's.
pub fn run<I>(args: I) -> ExitCode
where
    I: IntoIterator<Item = OsString>,
{
    let cli = match Cli::try_parse_from(args) {
        Ok(cli) => cli,
        Err(error) => error.exit(),
    };
    let stdout = io::stdout();
    let stderr = io::stderr();
    let (mut out, mut err) = (stdout.lock(), stderr.lock());
    let code = match cli.command {
        Command::Version => writeln!(out, "{}", version::line()).map(|()| 0),
        Command::Docs { topic, json } => docs::run(topic.as_deref(), json, &mut out, &mut err),
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
