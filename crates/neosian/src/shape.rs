//! Installation ownership and PATH findings (`_cli/shape.py`).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

pub fn kind(env: &BTreeMap<String, String>) -> &'static str {
    if env.get("NEOSIAN_INSTALL").is_some_and(|s| s == "container") {
        "container"
    } else {
        "binary"
    }
}

pub fn upgrade(kind: &str, version: &str) -> String {
    if kind == "container" {
        format!("docker pull ghcr.io/mausa-ai/neosian:{version}")
    } else {
        "neosian-update".into()
    }
}

pub fn executable(path: &Path) -> bool {
    if !path.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        path.metadata()
            .is_ok_and(|m| m.permissions().mode() & 0o111 != 0)
    }
    #[cfg(not(unix))]
    {
        true
    }
}

pub fn paths(env: &BTreeMap<String, String>, cwd: &Path) -> Vec<String> {
    let Some(paths) = env.get("PATH").filter(|p| !p.is_empty()) else {
        return vec![];
    };
    let mut names = vec!["neosian".to_string()];
    if cfg!(windows) {
        names.extend(
            env.get("PATHEXT")
                .map(String::as_str)
                .unwrap_or(".COM;.EXE;.BAT;.CMD")
                .split(';')
                .map(|ext| format!("neosian{ext}")),
        );
    }
    let mut seen: Vec<PathBuf> = vec![];
    let mut found = vec![];
    for directory in std::env::split_paths(paths) {
        for name in &names {
            let candidate: PathBuf = cwd.join(&directory).join(name).components().collect();
            if executable(&candidate)
                && let Ok(real) = candidate.canonicalize()
                && !seen.contains(&real)
            {
                seen.push(real);
                found.push(candidate.display().to_string());
            }
        }
    }
    found
}

pub fn shadowing(paths: &[String]) -> Vec<String> {
    if paths.len() < 2 {
        return vec![];
    }
    vec![format!(
        "PATH selects {}; also found {}. Run an absolute path to choose an installation",
        paths[0],
        paths[1..].join(", ")
    )]
}
