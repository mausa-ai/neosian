//! Invocation-only locations and the executable to register.

use std::collections::BTreeMap;
use std::env;
use std::io;
use std::path::{Component, Path, PathBuf};

use crate::repr::repr;

pub const CLIENTS: [&str; 6] = [
    "claude-code",
    "codex",
    "opencode",
    "muse-code",
    "cursor",
    "pi",
];

pub struct Context {
    pub home: PathBuf,
    pub cwd: PathBuf,
    pub executable: String,
    pub env: BTreeMap<String, String>,
    pub directories: BTreeMap<String, PathBuf>,
}

impl Context {
    pub fn new() -> io::Result<Self> {
        Ok(Self {
            home: env::home_dir().ok_or_else(|| io::Error::other("no home directory"))?,
            cwd: env::current_dir()?,
            executable: env::current_exe()?.display().to_string(),
            env: env::vars().collect(),
            directories: BTreeMap::new(),
        })
    }

    pub fn locations(&mut self, values: &[String]) -> Result<(), String> {
        for value in values {
            let (client, directory) = value.split_once('=').unwrap_or(("", ""));
            if !CLIENTS.contains(&client) || directory.trim().is_empty() {
                return Err(
                    "--at requires CLIENT=DIR with a supported client and directory".into(),
                );
            }
            if self.directories.contains_key(client) {
                return Err(format!("--at repeats {client}"));
            }
            let path = absolute(&self.cwd, &expand(directory, &self.home));
            match client {
                "claude-code" | "codex" => {
                    let variable = if client == "codex" {
                        "CODEX_HOME"
                    } else {
                        "CLAUDE_CONFIG_DIR"
                    };
                    self.env.insert(variable.into(), path.display().to_string());
                }
                _ => {}
            }
            self.directories.insert(client.into(), path);
        }
        Ok(())
    }

    pub fn binary(&mut self, value: Option<&str>) -> Result<(), String> {
        if let Some(value) = value {
            let path = expand(value, &self.home);
            if !path.is_file() {
                return Err(format!("--binary {}: not a file", repr(value)));
            }
            self.executable = absolute(&self.cwd, &path).display().to_string();
        }
        Ok(())
    }

    pub fn directory(&self, client: &str) -> PathBuf {
        if let Some(path) = self.directories.get(client) {
            return path.clone();
        }
        let (variable, default) = match client {
            "claude-code" => ("CLAUDE_CONFIG_DIR", ".claude"),
            "codex" => ("CODEX_HOME", ".codex"),
            "opencode" => ("OPENCODE_CONFIG_DIR", ".config/opencode"),
            "pi" => ("PI_CODING_AGENT_DIR", ".pi/agent"),
            "cursor" => ("", ".cursor"),
            "claude-desktop" => {
                return if cfg!(target_os = "macos") {
                    self.home.join("Library/Application Support/Claude")
                } else if cfg!(windows) {
                    self.env
                        .get("APPDATA")
                        .filter(|v| !v.is_empty())
                        .map_or_else(|| self.home.join("AppData/Roaming"), PathBuf::from)
                        .join("Claude")
                } else {
                    self.home.join(".config/Claude")
                };
            }
            "muse-code" => {
                return self
                    .env
                    .get("XDG_CONFIG_HOME")
                    .filter(|v| !v.is_empty())
                    .map_or_else(|| self.home.join(".config"), PathBuf::from)
                    .join("muse");
            }
            _ => unreachable!("validated client"),
        };
        self.env
            .get(variable)
            .filter(|v| !v.is_empty())
            .map_or_else(
                || self.home.join(default),
                |value| {
                    if client == "pi" {
                        expand(value, &self.home)
                    } else {
                        value.into()
                    }
                },
            )
    }
}

pub fn expand(value: &str, home: &Path) -> PathBuf {
    Path::new(value)
        .strip_prefix("~")
        .map_or_else(|_| value.into(), |tail| home.join(tail))
}

/// Resolve existing symlinks and a not-yet-created tail, like Path.resolve().
pub fn absolute(cwd: &Path, path: &Path) -> PathBuf {
    let mut resolved = if path.is_absolute() {
        PathBuf::new()
    } else {
        cwd.to_path_buf()
    };
    for part in path.components() {
        match part {
            Component::CurDir => {}
            Component::ParentDir => {
                resolved.pop();
            }
            _ => {
                resolved.push(part);
                if let Ok(real) = resolved.canonicalize() {
                    resolved = real;
                }
            }
        }
    }
    resolved
}

/// Python shlex.join, including its choice of single quotes (the bytes are a pin).
pub fn shell_join(argv: &[String]) -> String {
    argv.iter()
        .map(|word| {
            if !word.is_empty()
                && word
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || "_@%+=:,./-".contains(c))
            {
                word.clone()
            } else {
                format!("'{}'", word.replace('\'', "'\"'\"'"))
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}
