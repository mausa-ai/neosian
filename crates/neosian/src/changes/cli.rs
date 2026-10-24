use super::{
    model::{Request, invalid},
    mounted,
};
use crate::memory::{dispatch::Memory, grammar::Flags, mounts::Refusal};
use crate::store::error::Error;
use clap::Subcommand;
use std::io::{self, Write};

#[derive(Subcommand)]
pub enum Command {
    /// Poll one mounted scope. Save the returned cursor, including on empty pages.
    Read {
        #[arg(default_value = "/memories")]
        path: String,
        #[arg(long)]
        cursor: Option<String>,
        #[arg(long, default_value_t = 50, value_parser = clap::value_parser!(u32).range(1..=100))]
        limit: u32,
        #[command(flatten)]
        flags: Flags,
    },
    /// Preview change retention through a cursor; --write removes retained events.
    Prune {
        #[arg(default_value = "/memories")]
        path: String,
        #[arg(long)]
        through: String,
        #[arg(long)]
        write: bool,
        #[command(flatten)]
        flags: Flags,
    },
}

pub fn run(command: &Command, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let (flags, path) = match command {
        Command::Read { flags, path, .. } | Command::Prune { flags, path, .. } => (flags, path),
    };
    let result = (|| -> Result<u8, Error> {
        let cwd = std::env::current_dir()?;
        let selected = flags.select(&cwd).map_err(|s| invalid(&s))?;
        let (mounts, actor) = flags
            .selected_settings(&cwd, &selected)
            .map_err(|r| match r {
                Refusal::Usage(s) => invalid(&s),
                Refusal::Memory(e) => Error::Memory(e),
            })?;
        mounted::select(&mounts, path)?;
        match command {
            Command::Read { cursor, limit, .. } => Request {
                cursor: cursor.clone(),
                limit: *limit as usize,
            }
            .validate()?,
            Command::Prune { through, .. } if through.is_empty() || through.len() > 2048 => {
                return Err(invalid("invalid retention cursor size"));
            }
            Command::Prune { .. } => {}
        }
        let memory = Memory {
            store: selected.open()?,
            mounts,
            actor,
        };
        let report = match command {
            Command::Read { cursor, limit, .. } => mounted::read(
                &memory,
                path,
                &Request {
                    cursor: cursor.clone(),
                    limit: *limit as usize,
                },
            )?,
            Command::Prune { through, write, .. } => {
                mounted::prune(&memory, path, through, *write)?
            }
        };
        writeln!(out, "{report}")?;
        Ok(0)
    })();
    crate::memory::operate::settle(result, flags.json, out, err)
}
