//! The `neosian memory` grammar (`memory/cli_grammar.py`, DESIGN §14.2):
//! argv to a typed command, pure. Nothing here builds a store or touches
//! the home, which is what makes exit 2 mean "nothing on disk". The six
//! commands' flags are the ladder's argument names, kebab-cased; the
//! operator verbs sit beside them and are never commands a model reaches.

use std::borrow::Cow;
use std::env;
use std::io::{self, Read};
use std::path::{Path, PathBuf};

use clap::{Args, Subcommand};

use super::mounts::{Mount, Refusal};
use crate::home::project_mounts;
use crate::repr::repr;
use crate::store::grammar::Actor;

pub const SCOPE_ENV: &str = "NEOSIAN_SCOPE";
/// Where the `--scope` sugar mounts: the path a trained memory tool roots at.
const SUGAR_MOUNT_PATH: &str = "memories";

/// The store flags every memory command takes.
#[derive(Args)]
pub struct Flags {
    /// FileStore root directory (created on start; default: the home,
    /// ~/.neosian or $NEOSIAN_HOME)
    #[arg(long)]
    pub root: Option<PathBuf>,
    /// single read-write mount of SCOPE at /memories
    #[arg(long)]
    scope: Option<String>,
    /// explicit mount; repeatable (ro = read-only, eo = edit-only:
    /// existing documents only)
    #[arg(long, value_name = "scope=...,path=...[,ro|,eo]")]
    mount: Vec<String>,
    /// who writes: <kind>:<id>[/...], recorded on every row
    #[arg(long, default_value = "cli:local")]
    actor: String,
    /// print the result as JSON on stdout
    #[arg(long)]
    pub json: bool,
}

#[derive(Subcommand)]
pub enum Command {
    /// print a document, a directory listing, or / (the index)
    View {
        /// virtual path
        #[arg(default_value = "/")]
        path: String,
        /// 1-based inclusive line range; END -1 means end of document
        #[arg(long, num_args = 2, value_names = ["START", "END"], allow_negative_numbers = true)]
        view_range: Option<Vec<i64>>,
        #[command(flatten)]
        flags: Flags,
    },
    /// create or overwrite a document
    Create {
        /// virtual path, e.g. /memories/topic-name
        path: String,
        /// document text ('-' = stdin)
        #[arg(long, allow_hyphen_values = true)]
        content: String,
        #[command(flatten)]
        flags: Flags,
    },
    /// replace exactly one occurrence
    #[command(name = "str_replace")]
    StrReplace {
        /// virtual path
        path: String,
        /// text to replace
        #[arg(long, allow_hyphen_values = true)]
        old_str: String,
        /// replacement text ('-' = stdin)
        #[arg(long, allow_hyphen_values = true)]
        new_str: String,
        #[command(flatten)]
        flags: Flags,
    },
    /// splice text at a line number
    Insert {
        /// virtual path
        path: String,
        /// 0-based line
        #[arg(long, allow_negative_numbers = true)]
        insert_line: i64,
        /// text to insert ('-' = stdin)
        #[arg(long, allow_hyphen_values = true)]
        insert_text: String,
        #[command(flatten)]
        flags: Flags,
    },
    /// delete a document
    Delete {
        /// virtual path
        path: String,
        #[command(flatten)]
        flags: Flags,
    },
    /// move a document (cross-mount is not atomic)
    Rename {
        /// current virtual path
        #[arg(value_name = "OLD")]
        old_path: String,
        /// destination virtual path
        #[arg(value_name = "NEW")]
        new_path: String,
        #[command(flatten)]
        flags: Flags,
    },
    /// list a document's version rows, newest first
    Versions {
        /// virtual document path
        path: String,
        /// most recent rows to show
        #[arg(
            long,
            value_name = "N",
            default_value_t = 50,
            allow_negative_numbers = true
        )]
        limit: i64,
        #[command(flatten)]
        flags: Flags,
    },
    /// clear a document's content everywhere, keeping the audit skeleton
    Redact {
        /// virtual document path (or a mount root + --all)
        path: String,
        /// required to redact a whole mount's scope (irreversible)
        #[arg(long)]
        all: bool,
        #[command(flatten)]
        flags: Flags,
    },
    /// undo one version row (must be the newest)
    Revert {
        /// virtual document path
        path: String,
        /// the version row to undo (from `versions`)
        #[arg(long, value_name = "N", allow_negative_numbers = true)]
        version: i64,
        #[command(flatten)]
        flags: Flags,
    },
}

impl Command {
    pub fn flags(&self) -> &Flags {
        match self {
            Self::View { flags, .. }
            | Self::Create { flags, .. }
            | Self::StrReplace { flags, .. }
            | Self::Insert { flags, .. }
            | Self::Delete { flags, .. }
            | Self::Rename { flags, .. }
            | Self::Versions { flags, .. }
            | Self::Redact { flags, .. }
            | Self::Revert { flags, .. } => flags,
        }
    }

    /// The guards the grammar owes beyond its shape, each a pure check of
    /// a string or a number.
    fn guard(&self) -> Result<(), Refusal> {
        let refuse = |message: String| Err(Refusal::Usage(message));
        match self {
            Self::Versions { limit, .. } if *limit < 1 => {
                refuse("--limit must be at least 1".to_string())
            }
            Self::Revert { version, .. } if *version < 1 => {
                refuse("--version must be at least 1 (versions start at 1)".to_string())
            }
            Self::Redact { path, all, .. } => {
                let root = !path.trim_matches('/').contains('/');
                match (root, all) {
                    (true, false) => refuse(format!(
                        "{} names a whole mount: pass --all to redact its entire scope \
                         (irreversible)",
                        repr(path)
                    )),
                    (false, true) => refuse(
                        "--all redacts a whole mount: pass the mount root, not a document"
                            .to_string(),
                    ),
                    _ => Ok(()),
                }
            }
            _ => Ok(()),
        }
    }

    /// The text of the one payload flag a command carries; `-` reads stdin
    /// whole. Python's stdin translates newlines on Windows alone (a CRLF
    /// or a lone CR becomes a newline there), and so does this.
    pub fn payload(&self) -> io::Result<Cow<'_, str>> {
        let value = match self {
            Self::Create { content, .. } => content,
            Self::StrReplace { new_str, .. } => new_str,
            Self::Insert { insert_text, .. } => insert_text,
            _ => return Ok(Cow::Borrowed("")),
        };
        if value != "-" {
            return Ok(Cow::Borrowed(value));
        }
        let mut text = String::new();
        io::stdin().read_to_string(&mut text)?;
        if cfg!(windows) {
            text = text.replace("\r\n", "\n").replace('\r', "\n");
        }
        Ok(Cow::Owned(text))
    }

    /// What the invocation resolves to, nothing built: the mounts its
    /// flags name (else `NEOSIAN_SCOPE` as the sugar mount, else the
    /// working directory's project layout) and its actor, each held to its
    /// grammar, then the command's own guards.
    pub fn settings(&self, cwd: &Path) -> Result<(Vec<Mount>, Actor), Refusal> {
        let flags = self.flags();
        if flags.scope.is_some() && !flags.mount.is_empty() {
            let sugar = "--scope is the single-mount sugar; use --mount for multi-mount";
            return Err(Refusal::Usage(sugar.to_string()));
        }
        let named = env::var(SCOPE_ENV).ok().filter(|scope| !scope.is_empty());
        let mounts = match flags.scope.as_ref().or(named.as_ref()) {
            _ if !flags.mount.is_empty() => {
                let parsed: Result<Vec<_>, _> =
                    flags.mount.iter().map(|m| Mount::parse(m)).collect();
                parsed?
            }
            Some(scope) => vec![Mount::new(scope, SUGAR_MOUNT_PATH, (false, false), "")?],
            None => project_mounts(cwd)?,
        };
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
        let actor = Actor::parse(&flags.actor).map_err(|reason| {
            let actor = repr(&flags.actor);
            Refusal::Usage(format!("--actor {actor}: {reason} (e.g. cli:claude-code)"))
        })?;
        self.guard()?;
        Ok((mounts, actor))
    }
}
