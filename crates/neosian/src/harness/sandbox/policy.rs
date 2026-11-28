//! The policy as data, and its macOS profile text. Every path in it is
//! canonical (symbolic links resolved, as the kernel compares them), comes
//! from the watcher's own validated inputs and never from the child, and
//! is refused when the profile language could not name it exactly.

use std::ffi::OsString;
use std::path::{Component, Path, PathBuf};

use super::super::adapter::{Agent, Mode};
use super::super::text::say;
use super::{state, store};

/// One path rule. A pattern is a whole anchored regular expression built
/// from an escaped canonical prefix.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Rule {
    Literal(String),
    Subpath(String),
    Pattern(String),
}

/// What the child may write, what it may never write even where an
/// allowance covers it, and the variables it is started with.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Policy {
    pub mode: Mode,
    pub allow: Vec<Rule>,
    pub protect: Vec<Rule>,
    /// Existing lock files a store reader opens for writing: data writes
    /// only, after the protected places (`store.rs`).
    pub locks: Vec<String>,
    pub env: Vec<(String, String)>,
    pub unset: Vec<&'static str>,
}

/// What the policy is built from, all of it the watcher's own.
pub struct Inputs<'a> {
    pub agent: Agent,
    pub mode: Mode,
    pub directory: &'a Path,
    pub private: &'a Path,
    pub home: &'a Path,
    /// Store roots and neosian's home: never writable by a child.
    pub protected: &'a [PathBuf],
    /// The stores' lock files.
    pub locks: &'a [PathBuf],
    /// The watcher's environment, for a harness's state directory override.
    pub var: &'a dyn Fn(&str) -> Option<OsString>,
}

/// The devices every process writes in normal use (the investigation's
/// prototype, which all three harnesses ran under).
const DEVICES: [&str; 5] = [
    "/dev/null",
    "/dev/zero",
    "/dev/tty",
    "/dev/dtracehelper",
    "/dev/ptmx",
];
const DEVICE_PATTERNS: [&str; 2] = ["^/dev/fd/[0-9]+$", "^/dev/ttys[0-9]+$"];

/// Places in the child's directory that configure a harness or Git, so a
/// `write` child cannot plant code that a later session runs. Listed in
/// both modes; in `read-only` the directory is not writable anyway.
const PROJECT_DIRS: [&str; 5] = [".git/hooks", ".claude", ".codex", ".opencode", ".agents"];
const PROJECT_FILES: [&str; 4] = [
    ".git/config",
    ".mcp.json",
    "opencode.json",
    "opencode.jsonc",
];

/// The path as profile text: UTF-8 without a quote, a backslash or a
/// control character, which the profile language could not name exactly.
pub fn text(path: &Path) -> Result<String, String> {
    match path.to_str() {
        Some(text)
            if !text
                .chars()
                .any(|c| c == '"' || c == '\\' || c.is_control()) =>
        {
            Ok(text.to_string())
        }
        _ => Err(say(
            "sandbox_path",
            &[("path", &path.display().to_string())],
        )),
    }
}

/// The text with every regular expression operator escaped.
pub fn escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        if "^$.|?*+()[]{}".contains(c) {
            out.push('\\');
        }
        out.push(c);
    }
    out
}

/// An absolute path made canonical: its longest existing ancestor resolved
/// by the operating system, then the rest appended. None for a relative
/// path or a `..` in the part that does not exist yet.
pub fn resolve(path: &Path) -> Option<PathBuf> {
    if !path.is_absolute() {
        return None;
    }
    let mut tail = Vec::new();
    let mut existing = path;
    loop {
        if let Ok(found) = existing.canonicalize() {
            let mut out = found;
            for name in tail.iter().rev() {
                out.push(name);
            }
            return Some(out);
        }
        match existing.components().next_back() {
            Some(Component::Normal(name)) => tail.push(name.to_os_string()),
            _ => return None,
        }
        existing = existing.parent()?;
    }
}

fn canonical_dir(path: &Path, key: &str) -> Result<PathBuf, String> {
    path.canonicalize()
        .ok()
        .filter(|path| path.is_dir())
        .ok_or_else(|| say(key, &[("path", &path.display().to_string())]))
}

pub fn build(inputs: &Inputs) -> Result<Policy, String> {
    let home = canonical_dir(inputs.home, "sandbox_home")?;
    let private = canonical_dir(inputs.private, "sandbox_path")?;
    let directory = canonical_dir(inputs.directory, "sandbox_path")?;
    let mut allow: Vec<Rule> = DEVICES
        .iter()
        .map(|device| Rule::Literal(device.to_string()))
        .chain(DEVICE_PATTERNS.iter().map(|p| Rule::Pattern(p.to_string())))
        .collect();
    allow.push(Rule::Subpath(text(&private)?));
    let own = state::rules(inputs.agent, &home, inputs.var)?;
    allow.extend(own.allow);
    if inputs.mode == Mode::Write {
        allow.push(Rule::Subpath(text(&directory)?));
    }
    // The marker stays: removing it would hide the child from a server
    // that sees only TMPDIR.
    let mut protect = vec![Rule::Literal(text(&private.join(super::super::MARKER))?)];
    for (names, dir) in [(&PROJECT_DIRS[..], true), (&PROJECT_FILES[..], false)] {
        for name in names {
            protect.extend(guarded(&directory.join(name), dir)?);
        }
    }
    for root in inputs.protected {
        if let Some(root) = resolve(root) {
            protect.push(Rule::Subpath(text(&root)?));
        }
    }
    protect.extend(own.protect);
    let mut locks = Vec::new();
    for lock in inputs.locks {
        if let Some(lock) = lock_file(lock) {
            locks.push(text(&lock)?);
        }
    }
    let tmp = text(&private)?;
    let mut env = vec![("TMPDIR".to_string(), tmp.clone())];
    if let Some(name) = own.tmp_var {
        env.push((name.to_string(), tmp));
    }
    let unset = match inputs.mode {
        Mode::ReadOnly => store::CREDENTIAL_VARS.to_vec(),
        Mode::Write => Vec::new(),
    };
    Ok(Policy {
        mode: inputs.mode,
        allow,
        protect,
        locks,
        env,
        unset,
    })
}

/// A lock file named canonically, only when it is not a link leading
/// elsewhere.
fn lock_file(lock: &Path) -> Option<PathBuf> {
    let name = lock.file_name()?;
    let parent = resolve(lock.parent()?)?;
    resolve(lock).filter(|found| *found == parent.join(name))
}

/// A protected place, and where it leads when it is already a link.
fn guarded(path: &Path, dir: bool) -> Result<Vec<Rule>, String> {
    let rule = |path: &Path| -> Result<Rule, String> {
        let path = text(path)?;
        Ok(if dir {
            Rule::Subpath(path)
        } else {
            Rule::Literal(path)
        })
    };
    let mut rules = vec![rule(path)?];
    if let Some(target) = resolve(path).filter(|target| target != path) {
        rules.push(rule(&target)?);
    }
    Ok(rules)
}

fn filter(rule: &Rule) -> String {
    match rule {
        Rule::Literal(path) => format!("(literal \"{path}\")"),
        Rule::Subpath(path) => format!("(subpath \"{path}\")"),
        Rule::Pattern(pattern) => format!("(regex #\"{pattern}\")"),
    }
}

impl Policy {
    /// The macOS sandbox profile: everything allowed but writes, then the
    /// allowances, then the protected places, which win because a later
    /// rule takes precedence, then data writes to the lock files; preference
    /// writes through cfprefsd closed.
    pub fn profile(&self) -> String {
        let mut out = String::from("(version 1)\n(allow default)\n(deny file-write*)\n");
        for (verb, rules) in [("allow", &self.allow), ("deny", &self.protect)] {
            if rules.is_empty() {
                continue;
            }
            out.push_str(&format!("({verb} file-write*"));
            for rule in rules {
                out.push_str("\n  ");
                out.push_str(&filter(rule));
            }
            out.push_str(")\n");
        }
        if !self.locks.is_empty() {
            out.push_str("(allow file-write-data");
            for lock in &self.locks {
                out.push_str(&format!("\n  (literal \"{lock}\")"));
            }
            out.push_str(")\n");
        }
        out.push_str(
            "(deny mach-lookup (global-name \"com.apple.cfprefsd.agent\") \
             (global-name \"com.apple.cfprefsd.daemon\"))\n",
        );
        out
    }
}
