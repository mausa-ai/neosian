//! Shell boundaries for the documented statusline command, not model execution.

use std::process::Command;

pub fn wrapper(argv: &[String]) -> String {
    #[cfg(windows)]
    {
        powershell_wrapper(argv)
    }
    #[cfg(not(windows))]
    {
        super::context::shell_join(argv)
    }
}

#[cfg(any(windows, test))]
fn powershell_wrapper(argv: &[String]) -> String {
    use base64::{Engine, engine::general_purpose::STANDARD};
    // An encoded literal invocation works whether Claude's outer shell is Git
    // Bash or PowerShell. It carries paths/arguments only, never host input.
    let arguments = argv
        .iter()
        .map(|s| format!("'{}'", s.replace('\'', "''")))
        .collect::<Vec<_>>()
        .join(" ");
    let script = format!(
        "$OutputEncoding = [Console]::InputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new(); & {arguments}"
    );
    let bytes: Vec<_> = script.encode_utf16().flat_map(u16::to_le_bytes).collect();
    format!(
        "powershell -NoLogo -NoProfile -NonInteractive -EncodedCommand {}",
        STANDARD.encode(bytes)
    )
}

#[cfg(windows)]
fn git_bash() -> Option<std::path::PathBuf> {
    use std::path::PathBuf;
    let env: std::collections::BTreeMap<_, _> = std::env::vars().collect();
    let mut candidates = Vec::new();
    if let Some(path) = env.get("CLAUDE_CODE_GIT_BASH_PATH") {
        candidates.push(PathBuf::from(path));
    }
    if let Some(git) = super::runner::which("git", &env)
        && let Some(parent) = git.parent()
    {
        candidates.push(parent.join("bash.exe"));
        candidates.push(parent.join("../bin/bash.exe"));
        candidates.push(parent.join("../../bin/bash.exe"));
    }
    for key in [
        "ProgramFiles",
        "PROGRAMFILES",
        "ProgramFiles(x86)",
        "LOCALAPPDATA",
    ] {
        if let Some(root) = env.get(key) {
            candidates.push(PathBuf::from(root).join("Git/bin/bash.exe"));
            candidates.push(PathBuf::from(root).join("Programs/Git/bin/bash.exe"));
        }
    }
    candidates.into_iter().find(|p| p.is_file())
}

pub fn delegate(command: &str) -> Command {
    #[cfg(windows)]
    {
        if let Some(bash) = git_bash() {
            let mut child = Command::new(bash);
            child.args(["-c", command]);
            return child;
        }
        let mut child = Command::new("powershell");
        child.args([
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ]);
        child
    }
    #[cfg(not(windows))]
    {
        let mut child = Command::new("bash");
        child.args(["-c", command]);
        child
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::{Engine, engine::general_purpose::STANDARD};

    #[test]
    fn windows_paths_and_shell_metacharacters_remain_literal_arguments() {
        let command = powershell_wrapper(&[
            "C:\\Users\\A's $name\\neosian.exe".into(),
            "--bridge".into(),
            "C:\\a b\\$(whoami);`x`".into(),
        ]);
        let bytes = STANDARD
            .decode(command.split_whitespace().last().unwrap())
            .unwrap();
        let words: Vec<_> = bytes
            .as_chunks::<2>()
            .0
            .iter()
            .copied()
            .map(u16::from_le_bytes)
            .collect();
        let script = String::from_utf16(&words).unwrap();
        assert!(
            script.ends_with(
                r"& 'C:\Users\A''s $name\neosian.exe' '--bridge' 'C:\a b\$(whoami);`x`'"
            )
        );
        assert!(!command.contains('$') && !command.contains('`') && !command.contains(';'));
    }
}
