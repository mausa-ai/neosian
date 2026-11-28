//! The common filesystem policy neosian applies to every harness child on
//! macOS (X4b, ledger #433): one policy for Claude Code, Codex and
//! OpenCode, built from validated canonical paths, with writes denied by
//! default. A `read-only` child may write only its private temporary
//! directory, its harness's own explicit state and a few devices; a `write`
//! child may also write its validated directory, which is the one
//! allowance between the modes. The watcher applies the policy at launch
//! with the operating system's sandbox (`macos.rs`) and stays outside it;
//! when the policy cannot be built or applied, the child is not started.
//! The server prepares the same policy before any client command runs, and
//! runs the client's metadata commands under it (X4e P4). Other platforms
//! keep each harness's own preset and say so.

#[cfg(test)]
mod hardening_tests;
#[cfg(test)]
mod launch_tests;
mod macos;
mod policy;
mod private;
mod state;
mod store;
#[cfg(test)]
mod tests;

use std::ffi::OsStr;
use std::path::Path;
use std::process::Command;

use super::adapter::{Agent, Mode};
use super::text::say;
pub use policy::{Inputs, Policy};
pub use private::Private;

/// How a platform's children are bounded.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Platform {
    /// macOS: neosian's common policy, applied by the operating system.
    Common,
    /// Elsewhere (Linux): each harness's own preset, as before X4.
    Presets,
}

/// The decision for an operating system named as `std::env::consts::OS`.
pub fn platform_for(os: &str) -> Platform {
    match os {
        "macos" => Platform::Common,
        _ => Platform::Presets,
    }
}

pub fn platform() -> Platform {
    platform_for(std::env::consts::OS)
}

/// The note a child's answer carries where the common policy is not
/// applied, so the parent knows which boundary holds.
pub fn note(platform: Platform, agent: Agent, mode: Mode) -> Option<String> {
    (platform == Platform::Presets).then(|| {
        say(
            "policy_presets",
            &[("agent", agent.name()), ("mode", mode.name())],
        )
    })
}

/// The server's admission, before any client command runs (X4e P4): on
/// macOS the turn's policy prepared and proved in a private directory of
/// its own, which lives as long as the admission, so the client's metadata
/// commands run under it; and the notes the child's answers carry.
pub struct Admitted {
    pub launch: Launch,
    pub notes: Vec<String>,
    _private: Option<Private>,
}

pub fn admit(
    agent: Agent,
    mode: Mode,
    selection: &[String],
    directory: &Path,
) -> Result<Admitted, String> {
    if platform() == Platform::Presets {
        return Ok(Admitted {
            launch: Launch::Presets,
            notes: Vec::new(),
            _private: None,
        });
    }
    let private = Private::make()
        .map_err(|error| say("sandbox_refused", &[("error", &error.to_string())]))?;
    let turn = Turn {
        agent,
        mode,
        directory,
        private: private.path(),
        selection,
    };
    let (launch, credential) = confine(&turn, Path::new(macos::FACILITY))?;
    Ok(Admitted {
        launch,
        notes: credential
            .then(|| say("sandbox_no_store", &[]))
            .into_iter()
            .collect(),
        _private: Some(private),
    })
}

/// One turn, as the watcher checked it.
pub struct Turn<'a> {
    pub agent: Agent,
    pub mode: Mode,
    /// The validated, canonical directory the child works in.
    pub directory: &'a Path,
    /// The turn's private directory (mode 0700, holding the child marker).
    pub private: &'a Path,
    /// The parent's store selection flags.
    pub selection: &'a [String],
}

/// How one turn's harness is started.
#[derive(Debug)]
pub enum Launch {
    /// Under the common policy, which a probe showed refuses a write.
    Confined {
        profile: String,
        policy: Box<Policy>,
    },
    /// With the harness's own preset only.
    Presets,
}

/// The turn's launch: on macOS the policy built, applied to a probe write
/// that it must refuse, and only then returned; any failure refuses the
/// turn with a sentence, and nothing is started.
pub fn prepare(turn: &Turn) -> Result<Launch, String> {
    match platform() {
        Platform::Presets => Ok(Launch::Presets),
        Platform::Common => confine(turn, Path::new(macos::FACILITY)).map(|(launch, _)| launch),
    }
}

/// The confined launch, and whether the parent's store is reached with a
/// credential the child does not get.
fn confine(turn: &Turn, facility: &Path) -> Result<(Launch, bool), String> {
    let stores = store::check(turn.selection, turn.directory)?;
    let home = super::adapter::home().ok_or_else(|| say("sandbox_home", &[]))?;
    let var = |name: &str| std::env::var_os(name);
    store::ready(&stores);
    let policy = policy::build(&Inputs {
        agent: turn.agent,
        mode: turn.mode,
        directory: turn.directory,
        private: turn.private,
        home: &home,
        stores: &stores,
        var: &var,
    })?;
    let profile = policy.profile();
    let target = macos::target(&policy, turn.private, &home)
        .ok_or_else(|| say("sandbox_unenforced", &[]))?;
    macos::probe(facility, &profile, &target)?;
    let policy = Box::new(policy);
    Ok((Launch::Confined { profile, policy }, stores.credential))
}

impl Launch {
    pub fn confined(&self) -> bool {
        matches!(self, Self::Confined { .. })
    }

    /// The command that starts `program` with `args`: the adapter's
    /// variables, then the policy's, then the policy's removals.
    pub fn command(
        &self,
        program: &Path,
        args: &[impl AsRef<OsStr>],
        env: &[(String, String)],
    ) -> Command {
        let mut command = match self {
            Self::Presets => Command::new(program),
            Self::Confined { profile, .. } => {
                macos::command(Path::new(macos::FACILITY), profile, program)
            }
        };
        command.args(args).envs(env.iter().map(|(k, v)| (k, v)));
        if let Self::Confined { policy, .. } = self {
            command.envs(policy.env.iter().map(|(k, v)| (k, v)));
        }
        self.strip(&mut command);
        command
    }

    /// Whether the child could write `path`, or replace it through its
    /// directory: the path, where it is and where it leads lie where an
    /// allowance reaches (X4f Q5). Protected places are not subtracted.
    pub fn writable(&self, path: &Path) -> bool {
        let Self::Confined { policy, .. } = self else {
            return false;
        };
        let placed = path
            .parent()
            .and_then(|dir| dir.canonicalize().ok())
            .zip(path.file_name())
            .map(|(dir, name)| dir.join(name));
        [Some(path.to_path_buf()), placed, path.canonicalize().ok()]
            .into_iter()
            .flatten()
            .any(|place| policy.allow.iter().any(|rule| macos::covers(rule, &place)))
    }

    /// The child's environment restrictions on a command that runs outside
    /// the policy, such as the client's sign-in status (X4e P4).
    pub fn strip(&self, command: &mut Command) {
        if let Self::Confined { policy, .. } = self {
            for name in &policy.unset {
                command.env_remove(name);
            }
        }
    }
}
