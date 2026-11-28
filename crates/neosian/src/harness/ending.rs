//! How a watcher ends what a turn started (§40.50 items 3 and 14, amended
//! by #431), best effort: the harness's descendants walked while it runs,
//! each signalled with the group it leads, then the harness, and once more
//! after the grace period for what is still the same process or appeared;
//! after a normal exit, what is left in the watcher's group is ended after
//! a longer grace that the answer does not wait for.

use std::collections::BTreeSet;
use std::fs::File;
use std::io::Write;
use std::mem::ManuallyDrop;
use std::os::fd::{AsRawFd, FromRawFd};
use std::process::Child;
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

use super::tree::{self, Row, SIGKILL, SIGTERM};
use super::watch::{EXIT_LINE, GRACE};

const SIG_IGN: usize = 1;
/// After a normal exit, how long late work in the group (a record hook
/// landing the turn) may take before it is ended.
const LATE: Duration = Duration::from_secs(20);

unsafe extern "C" {
    fn signal(signum: i32, handler: usize) -> usize;
    fn dup2(from: i32, to: i32) -> i32;
}

/// Ignore SIGTERM in the watcher, which sends it to its own group.
pub fn ignore_sigterm() {
    // SAFETY: signal(2) with SIG_IGN installs no handler of ours.
    unsafe {
        signal(SIGTERM, SIG_IGN);
    }
}

/// SIGTERM back to its default in a process the watcher starts after it
/// ignored the signal (the record verb, and the lander that verb starts).
pub fn restore_sigterm(command: &mut std::process::Command) {
    use std::os::unix::process::CommandExt;
    // SAFETY: the closure runs in the forked child before `exec` and calls
    // only signal(2) with SIG_DFL, which is async-signal-safe.
    unsafe {
        command.pre_exec(|| {
            signal(SIGTERM, 0);
            Ok(())
        });
    }
}

/// Write the exit line with the turn's nonce on stderr, then point standard
/// output and error at /dev/null, so the server's readers reach their end
/// while the watcher still cleans up.
pub fn report(nonce: &str, code: u8) {
    let _ = writeln!(raw(2), "\n{EXIT_LINE} {nonce} {code}");
    if let Ok(null) = std::fs::OpenOptions::new().write(true).open("/dev/null") {
        for fd in [1, 2] {
            // SAFETY: dup2(2) replaces descriptor 1 or 2 with /dev/null; the
            // watcher writes nothing more to either.
            unsafe {
                dup2(null.as_raw_fd(), fd);
            }
        }
    }
}

/// Descriptor 1 or 2 as a file that is never closed.
pub fn raw(fd: i32) -> ManuallyDrop<File> {
    // SAFETY: descriptors 1 and 2 stay open for the watcher's life (at the
    // end they point at /dev/null); ManuallyDrop never closes them.
    ManuallyDrop::new(unsafe { File::from_raw_fd(fd) })
}

/// The group's members other than the watcher, and their descendants.
fn group(rows: &[Row], own: i32) -> BTreeSet<i32> {
    let members: Vec<i32> = tree::members(rows, own)
        .into_iter()
        .filter(|pid| *pid != own)
        .collect();
    tree::descendants(rows, &members)
}

/// SIGTERM to `first`, up to `grace` for them and `done` to go, then
/// SIGKILL to what is still the same process or appeared in the group.
fn end(
    own: i32,
    rows: Vec<Row>,
    first: BTreeSet<i32>,
    last: Option<i32>,
    grace: Duration,
    done: &mut dyn FnMut() -> bool,
) {
    tree::signal_all(&rows, &first, own, last, SIGTERM);
    tree::signal(-own, SIGTERM);
    let deadline = Instant::now() + grace;
    while Instant::now() < deadline {
        if done() && !first.iter().any(|pid| tree::exists(*pid)) {
            break;
        }
        thread::sleep(Duration::from_millis(50));
    }
    let now = tree::table();
    let mut left = tree::still(&rows, &now, &first);
    left.extend(group(&now, own));
    let roots: Vec<i32> = left.iter().copied().collect();
    left.extend(tree::descendants(&now, &roots));
    tree::signal_all(&now, &left, own, None, SIGKILL);
}

/// End the running harness and everything it started that can be found.
pub fn end_tree(harness: &mut Child, own: i32) {
    let pid = harness.id() as i32;
    let rows = tree::table();
    let mut first = tree::descendants(&rows, &[pid]);
    first.extend(group(&rows, own));
    end(own, rows, first, Some(pid), GRACE, &mut || {
        !matches!(harness.try_wait(), Ok(None))
    });
    let _ = harness.kill();
    let _ = harness.wait();
}

/// After a normal exit: late work in the group may finish within `LATE`,
/// or until the server closes the pipe; then what remains is ended.
pub fn leftovers(own: i32, closed: &mpsc::Receiver<()>) {
    let deadline = Instant::now() + LATE;
    let mut rows = tree::table();
    while !group(&rows, own).is_empty() && Instant::now() < deadline {
        match closed.recv_timeout(Duration::from_millis(200)) {
            Err(mpsc::RecvTimeoutError::Timeout) => rows = tree::table(),
            Ok(()) | Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
    let rows = tree::table();
    let left = group(&rows, own);
    if !left.is_empty() {
        end(own, rows, left, None, Duration::from_secs(1), &mut || true);
    }
}
