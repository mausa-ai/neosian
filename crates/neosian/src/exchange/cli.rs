use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};

use clap::{Args, Subcommand};
use serde_json::{Value, json};

use super::model::{Bundle, Checkpoint, MAX_BYTES};
use crate::knowledge::{json::parse, model::invalid};
use crate::memory::{grammar::Flags, mounts::Refusal};
use crate::store::{FileStore, error::Error, fileio};

#[derive(Args)]
pub struct Export {
    /// Exactly one mounted source scope, such as /project.
    path: String,
    /// New private JSON bundle file; existing files are never overwritten.
    output: PathBuf,
    /// Confirm all source writers are paused for this export.
    #[arg(long)]
    paused: bool,
    #[command(flatten)]
    flags: Flags,
}

#[derive(Args)]
pub struct Receiver {
    /// Explicit local replica root, separate from your working memory.
    #[arg(long)]
    root: PathBuf,
    /// Exact source and receiver scope; no remapping.
    #[arg(long)]
    scope: String,
    #[arg(long)]
    json: bool,
}

#[derive(Subcommand)]
pub enum Command {
    /// Export current permitted memory; pause source writers first.
    Snapshot(Export),
    /// Export changes since a previous bundle or checkpoint JSON file.
    Delta {
        #[command(flatten)]
        export: Export,
        #[arg(long)]
        after: PathBuf,
    },
    /// Validate a bundle and show its paths, erasures and checkpoints.
    Inspect { bundle: PathBuf },
    /// Preview a bundle; --write atomically advances the local replica.
    Apply {
        bundle: PathBuf,
        #[command(flatten)]
        receiver: Receiver,
        #[arg(long)]
        write: bool,
    },
    /// Recover and inspect the receiver's durable checkpoint and conflicts.
    Status {
        #[command(flatten)]
        receiver: Receiver,
        /// Print only the checkpoint, for delta --after.
        #[arg(long)]
        checkpoint_only: bool,
    },
}

fn read(path: &Path) -> Result<String, Error> {
    let mut bytes = vec![];
    fs::File::open(path)?
        .take(MAX_BYTES as u64 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > MAX_BYTES {
        return Err(invalid("exchange input exceeds 20 MiB"));
    }
    String::from_utf8(bytes).map_err(|_| invalid("exchange requires UTF-8 JSON"))
}

fn bundle(path: &Path) -> Result<Bundle, Error> {
    let bundle: Bundle = parse(&read(path)?)?;
    bundle.validate()?;
    Ok(bundle)
}

fn after(path: &Path) -> Result<Checkpoint, Error> {
    let value: Value = parse(&read(path)?)?;
    let checkpoint = if value.get("checkpoint").is_some() {
        let bundle: Bundle = serde_json::from_value(value).map_err(|e| invalid(e.to_string()))?;
        bundle.validate()?;
        bundle.checkpoint
    } else {
        serde_json::from_value(value).map_err(|e| invalid(e.to_string()))?
    };
    checkpoint.validate()?;
    Ok(checkpoint)
}

fn summary(bundle: &Bundle) -> Value {
    json!({"format":1, "bundle_id":bundle.id, "kind":if bundle.base.is_some() {"delta"} else {"snapshot"},
        "base":bundle.base, "checkpoint":bundle.checkpoint, "erasures":bundle.erasures,
        "updates":bundle.updates.iter().map(|u| json!({"path":u.path,
            "state":if u.deletion.is_some() {"deleted"} else if u.document.as_ref().is_some_and(|d| d.redacted) {"erased"} else {"present"},
            "version":u.document.as_ref().map(|d| d.version).or_else(|| u.deletion.as_ref().map(|d| d.version))})).collect::<Vec<_>>()})
}

fn export(args: &Export, base: Option<Checkpoint>) -> Result<Value, Error> {
    if !args.paused {
        return Err(invalid("pause all source writers, then pass --paused"));
    }
    if args.output.exists() {
        return Err(invalid("exchange output already exists"));
    }
    let cwd = std::env::current_dir()?;
    let selection = args.flags.select(&cwd).map_err(invalid)?;
    let (mounts, _) = args
        .flags
        .selected_settings(&cwd, &selection)
        .map_err(|e| match e {
            Refusal::Usage(message) => invalid(message),
            Refusal::Memory(e) => Error::Memory(e),
        })?;
    let mount = crate::changes::mounted::select(&mounts, &args.path)?;
    let store = selection.open()?;
    let bundle = super::export::collect(store.as_ref(), &mount.scope, base)?;
    let text = serde_json::to_string(&bundle).expect("bundle serializes");
    let mut output = fileio::private()
        .write(true)
        .create_new(true)
        .open(&args.output)?;
    output.write_all(text.as_bytes())?;
    output.sync_all()?;
    #[cfg(unix)]
    fs::File::open(
        args.output
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new(".")),
    )?
    .sync_all()?;
    Ok(summary(&bundle))
}

pub fn run(command: &Command, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let result = (|| -> Result<u8, Error> {
        let report = match command {
            Command::Snapshot(args) => export(args, None)?,
            Command::Delta {
                export: args,
                after: file,
            } => export(args, Some(after(file)?))?,
            Command::Inspect { bundle: path } => summary(&bundle(path)?),
            Command::Apply {
                bundle: path,
                receiver,
                write,
            } => {
                let bundle = bundle(path)?;
                crate::store::grammar::parse_scope(&receiver.scope)?;
                FileStore::open(&receiver.root)?.apply_exchange(&receiver.scope, &bundle, *write)?
            }
            Command::Status {
                receiver,
                checkpoint_only,
            } => {
                crate::store::grammar::parse_scope(&receiver.scope)?;
                let status = FileStore::open(&receiver.root)?.exchange_status(&receiver.scope)?;
                if *checkpoint_only {
                    if status["status"] != "ready" {
                        return Err(invalid("replica is not ready; inspect exchange status"));
                    }
                    status["checkpoint"].clone()
                } else {
                    status
                }
            }
        };
        writeln!(out, "{report}")?;
        Ok(0)
    })();
    crate::memory::operate::settle(result, true, out, err)
}
