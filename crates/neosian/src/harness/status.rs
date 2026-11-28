//! The sign-in check before a turn (X4c): the client's own status command
//! (`signin::command`) run by the server, outside any sandbox, in the
//! child's directory and the server's environment, so the answer is the
//! one the person would get, less the store credentials no child receives
//! (X4e P4): a status command may refresh and save its sign-in, which the
//! policy would refuse. It runs through the shared bounded runner
//! (`bounded.rs`, X4f Q6): 5 seconds, 8 KiB per stream, its group and
//! descendants ended. Its output is read and dropped: only
//! `signin::classify` sees it, and nothing of it is stored.

use std::path::Path;
use std::process::Command;
use std::time::Duration;

use super::adapter::Agent;
use super::bounded::{self, Unanswered};
use super::sandbox::Launch;
use super::signin::{self, Status};
use super::text::say;

/// How long a status command has to answer.
pub const BOUND: Duration = Duration::from_secs(5);
/// How much of each stream is kept.
const KEPT: usize = 8 * 1024;

/// What the client's status command says, or None for a client without one.
pub fn check(agent: Agent, program: &Path, dir: &Path, launch: &Launch) -> Option<Status> {
    let words = signin::command(agent)?;
    let mut command = Command::new(program);
    launch.strip(&mut command);
    command.args(words).current_dir(dir);
    Some(match bounded::run(command, BOUND, KEPT) {
        Ok(ran) => signin::classify(agent, ran.code, &ran.stdout, &ran.stderr),
        Err(Unanswered::Unstartable(error)) => {
            Status::Unknown(say("signin_unstartable", &[("error", &error)]))
        }
        Err(Unanswered::Timeout) => {
            let seconds = BOUND.as_secs().to_string();
            Status::Unknown(say("signin_timeout", &[("seconds", &seconds)]))
        }
    })
}
