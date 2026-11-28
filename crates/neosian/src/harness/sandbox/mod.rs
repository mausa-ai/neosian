//! The common filesystem policy neosian applies to every harness child on
//! macOS (X4b, ledger #433): one policy for Claude Code, Codex and
//! OpenCode, built from validated canonical paths, with writes denied by
//! default. A `read-only` child may write only its private temporary
//! directory, its harness's own explicit state and a few devices; a `write`
//! child may also write its validated directory, which is the one
//! allowance between the modes. The watcher applies the policy at launch
//! with the operating system's sandbox (`macos.rs`) and stays outside it;
//! when the policy cannot be built or applied, the child is not started.
//! Other platforms keep each harness's own preset and say so.

#[cfg(test)]
mod launch_tests;
mod macos;
mod policy;
mod state;
mod store;
#[cfg(test)]
mod tests;

use std::path::Path;
use std::process::Command;

use super::adapter::{Agent, Mode};
use super::text::say;
pub use policy::{Inputs, Policy};

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

/// What the server checks before it starts a turn's watcher, so `start`
/// and `send` refuse at once: on macOS, that the sandbox can be applied
/// from this process and that a read-only child can be kept from writing
/// the store. The watcher builds and applies the turn's own policy again.
pub fn admit(mode: Mode, selection: &[String], directory: &Path) -> Result<(), String> {
    match platform() {
        Platform::Presets => Ok(()),
        Platform::Common => {
            store::check(mode, selection, directory)?;
            macos::can_apply(Path::new(macos::FACILITY))
        }
    }
}

/// One turn, as the watcher checked it.
pub struct Turn<'a> {
    pub agent: Agent,
    pub mode: Mode,
    /// The validated, canonical directory the child works in.
    pub directory: &'a Path,
    /// The turn's private directory (mode 0700, holding the child marker).
    pub private: &'a Path,
    /// The server's store selection flags.
    pub selection: &'a [String],
}

/// How one turn's harness is started.
#[derive(Debug)]
pub enum Launch {
    /// Under the common policy, which a probe showed refuses a write.
    Confined { profile: String, policy: Policy },
    /// With the harness's own preset only.
    Presets,
}

/// The turn's launch: on macOS the policy built, applied to a probe write
/// that it must refuse, and only then returned; any failure refuses the
/// turn with a sentence, and nothing is started.
pub fn prepare(turn: &Turn) -> Result<Launch, String> {
    match platform() {
        Platform::Presets => Ok(Launch::Presets),
        Platform::Common => confine(turn, Path::new(macos::FACILITY)),
    }
}

fn confine(turn: &Turn, facility: &Path) -> Result<Launch, String> {
    let stores = store::check(turn.mode, turn.selection, turn.directory)?;
    let home = super::adapter::home().ok_or_else(|| say("sandbox_home", &[]))?;
    let var = |name: &str| std::env::var_os(name);
    store::ready(&stores);
    let policy = policy::build(&Inputs {
        agent: turn.agent,
        mode: turn.mode,
        directory: turn.directory,
        private: turn.private,
        home: &home,
        protected: &stores.protected,
        locks: &stores.locks,
        var: &var,
    })?;
    let profile = policy.profile();
    macos::probe(facility, &profile, turn.private)?;
    Ok(Launch::Confined { profile, policy })
}

impl Launch {
    pub fn confined(&self) -> bool {
        matches!(self, Self::Confined { .. })
    }

    /// The command that starts `program` with `args`: the adapter's
    /// variables, then the policy's, then the policy's removals.
    pub fn command(&self, program: &Path, args: &[String], env: &[(String, String)]) -> Command {
        let mut command = match self {
            Self::Presets => Command::new(program),
            Self::Confined { profile, .. } => {
                macos::command(Path::new(macos::FACILITY), profile, program)
            }
        };
        command.args(args).envs(env.iter().map(|(k, v)| (k, v)));
        if let Self::Confined { policy, .. } = self {
            command.envs(policy.env.iter().map(|(k, v)| (k, v)));
            for name in &policy.unset {
                command.env_remove(name);
            }
        }
        command
    }
}
