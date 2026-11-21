//! Ending a turn's processes (§40.50 item 3, amended by #430), best
//! effort: the process table read with `ps`, the harness's descendants
//! walked while it is alive, each signalled with its process group (Codex
//! starts every command and MCP server in a group of its own), then the
//! harness; once more after the grace period for anything that appeared. A
//! process that detached before a walk and left the group escapes.

use std::collections::{BTreeSet, HashMap};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

pub const SIGKILL: i32 = 9;
pub const SIGTERM: i32 = 15;

unsafe extern "C" {
    fn kill(pid: i32, sig: i32) -> i32;
}

/// Send `sig` to one process (`pid` > 0) or one process group (`pid` < 0).
pub fn signal(pid: i32, sig: i32) -> bool {
    if pid.unsigned_abs() <= 1 {
        return false;
    }
    // SAFETY: kill(2) takes two integers and reads no memory of ours.
    unsafe { kill(pid, sig) == 0 }
}

/// Whether a process exists (signal 0 checks without sending).
pub fn exists(pid: i32) -> bool {
    signal(pid, 0) || std::io::Error::last_os_error().raw_os_error() == Some(1)
}

/// One row of the process table, with the process's start time so a pid
/// seen earlier is signalled later only while it is the same process.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Row {
    pub pid: i32,
    pub ppid: i32,
    pub pgid: i32,
    pub start: String,
}

/// The process table, or empty when `ps` cannot be read.
pub fn table() -> Vec<Row> {
    let ps = ["/bin/ps", "/usr/bin/ps"]
        .into_iter()
        .find(|path| std::path::Path::new(path).exists());
    let Some(ps) = ps else {
        return Vec::new();
    };
    let Ok(output) = Command::new(ps)
        .args([
            "-A", "-o", "pid=", "-o", "ppid=", "-o", "pgid=", "-o", "lstart=",
        ])
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
    else {
        return Vec::new();
    };
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(row)
        .collect()
}

/// One line of `ps -o pid= -o ppid= -o pgid= -o lstart=`.
fn row(line: &str) -> Option<Row> {
    let mut words = line.split_whitespace();
    let pid = words.next()?.parse().ok()?;
    let ppid = words.next()?.parse().ok()?;
    let pgid = words.next()?.parse().ok()?;
    let start = words.collect::<Vec<_>>().join(" ");
    Some(Row {
        pid,
        ppid,
        pgid,
        start,
    })
}

/// The pids of `earlier` that are still the same processes in `now`: the
/// same pid with the same start time.
pub fn still(earlier: &[Row], now: &[Row], pids: &BTreeSet<i32>) -> BTreeSet<i32> {
    pids.iter()
        .copied()
        .filter(|pid| {
            let then = earlier.iter().find(|row| row.pid == *pid);
            let current = now.iter().find(|row| row.pid == *pid);
            matches!((then, current), (Some(then), Some(current)) if then.start == current.start)
        })
        .collect()
}

/// `roots` and every process descended from them.
pub fn descendants(rows: &[Row], roots: &[i32]) -> BTreeSet<i32> {
    let mut children: HashMap<i32, Vec<i32>> = HashMap::new();
    for row in rows {
        children.entry(row.ppid).or_default().push(row.pid);
    }
    let mut found = BTreeSet::new();
    let mut pending: Vec<i32> = roots.to_vec();
    while let Some(pid) = pending.pop() {
        if pid > 1 && found.insert(pid) {
            pending.extend(children.get(&pid).into_iter().flatten());
        }
    }
    found
}

/// The members of a process group.
pub fn members(rows: &[Row], pgid: i32) -> Vec<i32> {
    rows.iter()
        .filter(|row| row.pgid == pgid)
        .map(|row| row.pid)
        .collect()
}

/// Signal `pids`, every group one of them leads (except `spare`, the
/// caller's own), and `last` after the rest.
pub fn signal_all(rows: &[Row], pids: &BTreeSet<i32>, spare: i32, last: Option<i32>, sig: i32) {
    for row in rows.iter().filter(|row| pids.contains(&row.pid)) {
        if row.pgid == row.pid && row.pgid != spare {
            signal(-row.pgid, sig);
        }
    }
    for pid in pids
        .iter()
        .filter(|pid| Some(**pid) != last && **pid != spare)
    {
        signal(*pid, sig);
    }
    if let Some(last) = last {
        signal(last, sig);
    }
}

/// End the group `pgid` and everything descended from its members, from
/// outside it: what the server does when the watcher is gone. SIGTERM, up
/// to `grace` for them to go, then SIGKILL for what is left or appeared.
pub fn end_group(pgid: i32, grace: Duration) {
    let rows = table();
    let first = descendants(&rows, &members(&rows, pgid));
    if first.is_empty() {
        return;
    }
    signal_all(&rows, &first, 0, None, SIGTERM);
    signal(-pgid, SIGTERM);
    let deadline = Instant::now() + grace;
    while Instant::now() < deadline && first.iter().any(|pid| exists(*pid)) {
        thread::sleep(Duration::from_millis(50));
    }
    let earlier = rows;
    let rows = table();
    let mut left = still(&earlier, &rows, &first);
    left.extend(descendants(&rows, &members(&rows, pgid)));
    let roots: Vec<i32> = left.iter().copied().collect();
    left.extend(descendants(&rows, &roots));
    signal_all(&rows, &left, 0, None, SIGKILL);
    signal(-pgid, SIGKILL);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(pid: i32, ppid: i32, pgid: i32, start: &str) -> Row {
        Row {
            pid,
            ppid,
            pgid,
            start: start.to_string(),
        }
    }

    #[test]
    fn descendants_follow_parents_and_never_the_init_process() {
        let rows = [
            at(10, 1, 10, "Sat Nov 21 01:00:00 2026"),
            at(11, 10, 11, "Sat Nov 21 01:00:01 2026"),
            at(12, 11, 11, "Sat Nov 21 01:00:02 2026"),
            at(13, 1, 10, "Sat Nov 21 01:00:03 2026"),
            at(14, 2, 14, "Sat Nov 21 01:00:04 2026"),
        ];
        assert_eq!(descendants(&rows, &[10]), BTreeSet::from([10, 11, 12]));
        assert_eq!(descendants(&rows, &[1]), BTreeSet::new());
        assert_eq!(members(&rows, 10), [10, 13]);
        assert!(!signal(1, 0) && !signal(-1, 0) && !signal(0, 0));
        assert!(exists(std::process::id() as i32));
        let me = std::process::id() as i32;
        let now = table();
        assert!(now.iter().any(|row| row.pid == me && !row.start.is_empty()));
        assert_eq!(
            still(&now, &table(), &BTreeSet::from([me])),
            BTreeSet::from([me])
        );
        let reused = [at(11, 1, 11, "Sat Nov 21 02:00:00 2026"), rows[2].clone()];
        assert_eq!(
            still(&rows, &reused, &BTreeSet::from([10, 11, 12])),
            BTreeSet::from([12])
        );
        assert_eq!(
            row("  42     1    42 Sat Nov 21 01:24:51 2026"),
            Some(at(42, 1, 42, "Sat Nov 21 01:24:51 2026"))
        );
    }
}
