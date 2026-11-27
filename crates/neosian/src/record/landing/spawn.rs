//! The detached start of `neosian record-land` (DESIGN §40.54 item 17.3;
//! A.5.14 and owner question O5 of the T1e1 plan). The hook starts the
//! current executable with its three standard streams on the null device,
//! so a client that reads the hook's output to its end never waits for
//! the lander, and it does not wait for the child. On Unix the child calls
//! `setsid` before it runs the verb, so it leads a session and a process
//! group of its own: a signal to the hook's process group, or the hangup
//! of a closed terminal, does not reach it, and once the hook exits its
//! parent is the system's. On Windows it runs with `DETACHED_PROCESS |
//! CREATE_NEW_PROCESS_GROUP`, and the hook's own three standard handles
//! are made non-inheritable first, since a child inherits every
//! inheritable handle; it does not break away from a job object the
//! client chose (O5). The lander inherits the hook's environment (D13)
//! and runs in the home, so it holds no client directory open. A URL that
//! carries userinfo or a query reaches it in `NEOSIAN_LANDER_URL`, never
//! among its arguments, which any process may read (ruling G3).

use std::io;
use std::path::{Path, absolute};
use std::process::{Command, Stdio};

/// The variable that hands a lander the URL its hook was given.
pub const LANDER_URL: &str = "NEOSIAN_LANDER_URL";

/// What a lander is started with: the home, and the spool tree and URL
/// the hook names (rulings D4 and the T1e1c note on direct URLs).
pub struct Lander<'a> {
    pub home: &'a Path,
    pub spool: Option<&'a Path>,
    pub url: Option<&'a str>,
}

/// The command that runs `record-land` for `lander`, its URL in its
/// environment, a value the hook inherited removed when there is none.
pub fn command(lander: &Lander) -> io::Result<Command> {
    let home = absolute(lander.home)?;
    let mut command = Command::new(std::env::current_exe()?);
    command.arg("record-land").arg("--home").arg(&home);
    if let Some(spool) = lander.spool {
        command.arg("--spool").arg(absolute(spool)?);
    }
    match lander.url {
        Some(url) => command.env(LANDER_URL, url),
        None => command.env_remove(LANDER_URL),
    };
    command
        .current_dir(&home)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    Ok(command)
}

/// Starts `record-land` for `lander`, detached, and returns at once.
pub fn spawn(lander: &Lander) -> io::Result<()> {
    let mut command = command(lander)?;
    detach(&mut command);
    command.spawn().map(drop)
}

#[cfg(unix)]
fn detach(command: &mut Command) {
    use std::os::unix::process::CommandExt;
    unsafe extern "C" {
        fn setsid() -> i32;
    }
    // SAFETY: the closure runs in the forked child before `exec` and calls
    // only setsid(2), which is async-signal-safe and touches no memory of
    // ours; reading `errno` allocates nothing.
    unsafe {
        command.pre_exec(|| {
            if setsid() == -1 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
    }
}

#[cfg(windows)]
fn detach(command: &mut Command) {
    use std::os::windows::io::AsRawHandle;
    use std::os::windows::process::CommandExt;
    use windows_sys::Win32::Foundation::{HANDLE_FLAG_INHERIT, SetHandleInformation};
    use windows_sys::Win32::System::Threading::{CREATE_NEW_PROCESS_GROUP, DETACHED_PROCESS};
    let handles = [
        io::stdin().as_raw_handle(),
        io::stdout().as_raw_handle(),
        io::stderr().as_raw_handle(),
    ];
    for handle in handles {
        if !handle.is_null() {
            // SAFETY: the handle is this process's own standard handle; the
            // call changes only its inheritance flag, and a handle that
            // cannot take the flag is left as it is.
            unsafe { SetHandleInformation(handle, HANDLE_FLAG_INHERIT, 0) };
        }
    }
    command.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP);
}
