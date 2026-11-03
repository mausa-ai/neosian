//! Explicit registration is consent to subsequent bounded hook capture.

use super::{authority, config, error_text, invalid, local, scan, sync};
use crate::memory::grammar::Flags;
use crate::memory::mounts::{Refusal, resolve, structural, writable};
use crate::store::error::Error;
use clap::Subcommand;
use serde_json::{Value, json};
use std::io::{self, Write};
use std::path::PathBuf;

#[derive(Subcommand)]
pub enum Command {
    /// Preview a local source registration. --write consents to automatic capture.
    Add {
        name: String,
        #[arg(long, value_parser = ["claude-code", "codex"])]
        agent: String,
        /// Existing Markdown file or directory. Directories are shallow by default.
        #[arg(long)]
        source: PathBuf,
        /// Destination mount, e.g. /project or /user; copies use native-memory/NAME/.
        #[arg(long)]
        to: String,
        /// Include Markdown in subdirectories as well.
        #[arg(long)]
        recursive: bool,
        /// Permit capture from every project with this same mounted scope and store.
        #[arg(long, conflicts_with = "project")]
        all_projects: bool,
        /// Project whose hooks may capture this source (default: current directory).
        #[arg(long)]
        project: Option<PathBuf>,
        #[arg(long)]
        write: bool,
        #[command(flatten)]
        flags: Flags,
    },
    /// List local source registrations; does not read source content.
    List {
        #[arg(long)]
        json: bool,
    },
    /// Stop future capture; existing copies and source files remain.
    Remove {
        name: String,
        #[arg(long)]
        write: bool,
        #[arg(long)]
        json: bool,
    },
    /// Preview current copies; --write captures them using current mounted authority.
    Sync {
        name: String,
        #[arg(long)]
        write: bool,
        #[command(flatten)]
        flags: Flags,
    },
}

fn refusal(error: Refusal) -> Error {
    match error {
        Refusal::Usage(text) => invalid(text),
        Refusal::Memory(e) => e.into(),
    }
}

fn execute(command: &Command) -> Result<Value, Error> {
    let changing = matches!(
        command,
        Command::Add { write: true, .. }
            | Command::Remove { write: true, .. }
            | Command::Sync { write: true, .. }
    );
    let _held = if changing {
        Some(crate::store::lock::acquire(
            &config::path()?.with_extension("lock"),
        )?)
    } else {
        None
    };
    let mut registry = config::read()?;
    match command {
        Command::List { .. } => Ok(json!({"format":1,"sources":registry.sources})),
        Command::Remove { name, write, .. } => {
            let before = registry.sources.len();
            registry.sources.retain(|s| s.name != *name);
            if registry.sources.len() == before {
                return Err(invalid("native memory source is not registered"));
            }
            if *write {
                config::write(&registry)?;
            }
            Ok(json!({"success":true,"source":name,"removed":write,"preview":!write}))
        }
        Command::Add {
            name,
            agent,
            source,
            to,
            recursive,
            all_projects,
            project,
            write,
            flags,
        } => {
            config::name(name)?;
            if registry.sources.iter().any(|s| s.name == *name) {
                return Err(invalid(
                    "native memory name is already registered; remove it explicitly before changing its consent",
                ));
            }
            if registry.sources.len() >= config::MAX_SOURCES {
                return Err(invalid("native memory supports at most 16 sources"));
            }
            let cwd = std::env::current_dir()?;
            let project = config::absolute(project.as_deref().unwrap_or(&cwd))?;
            if !project.is_dir() {
                return Err(invalid(
                    "native memory project must be an existing directory",
                ));
            }
            let selection = flags.select(&project).map_err(invalid)?;
            let root = local(&selection)?;
            let (mounts, _) = flags
                .selected_settings(&project, &selection)
                .map_err(refusal)?;
            let (mount, rest) = resolve(&mounts, to)?;
            if !rest.is_empty() || to != &format!("/{}", mount.path) {
                return Err(invalid(
                    "--to must name one mount, such as /project or /user",
                ));
            }
            writable(mount)?;
            structural(mount)?;
            if std::fs::symlink_metadata(source)?.file_type().is_symlink() {
                return Err(invalid("native memory source cannot be a symlink"));
            }
            let source = config::absolute(source)?;
            if source.starts_with(&root) || root.starts_with(&source) {
                return Err(invalid(
                    "native memory source and destination store must not overlap",
                ));
            }
            let entry = config::Source {
                name: name.clone(),
                agent: agent.clone(),
                source,
                recursive: *recursive,
                project: (!all_projects).then_some(project),
                store: root,
                scope: mount.scope.clone(),
                destination: format!("{to}/native-memory/{name}"),
            };
            let files = scan::read(&entry)?;
            let planned: Vec<_> = files
                .iter()
                .map(|file| {
                    json!({"file":file.name,"bytes":file.content.len(),
                "path":format!("{}/{}",entry.destination,file.name)})
                })
                .collect();
            let result = json!({"success":true,"preview":!write,"registered":write,"registration":entry,
                "files":planned,"capture":"automatic_on_record_hooks","deletions":"preserve_copies"});
            if *write {
                registry.sources.push(entry);
                config::write(&registry)?;
            }
            Ok(result)
        }
        Command::Sync { name, write, flags } => {
            let source = registry
                .sources
                .iter()
                .find(|s| s.name == *name)
                .ok_or_else(|| invalid("native memory source is not registered"))?;
            let cwd = std::env::current_dir()?;
            let selection = flags.select(&cwd).map_err(invalid)?;
            let root = local(&selection)?;
            let (mounts, actor) = flags.selected_settings(&cwd, &selection).map_err(refusal)?;
            authority(source, &root, &mounts)?;
            sync(source, &root, &mounts, &actor, *write)
        }
    }
}

pub fn run(command: &Command, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let json = match command {
        Command::Add { flags, .. } | Command::Sync { flags, .. } => flags.json,
        Command::List { json } | Command::Remove { json, .. } => *json,
    };
    match execute(command) {
        Ok(report) => {
            let failed = report.get("success") == Some(&Value::Bool(false));
            if json {
                writeln!(out, "{report}")?;
            } else {
                writeln!(
                    out,
                    "{}",
                    serde_json::to_string_pretty(&report).map_err(io::Error::other)?
                )?;
            }
            Ok(u8::from(failed))
        }
        Err(error) => {
            let text = error_text(error);
            if json {
                writeln!(out, "{}", json!({"success":false,"error":text}))?;
            }
            writeln!(err, "error: {text}")?;
            Ok(1)
        }
    }
}
