//! The shell's TOML document and provider names (`_cli/config.py`, `providers.py`).

use crate::store::fileio;
use serde_json::{Value, json};
use std::fs;
use std::io;
use std::path::Path;

pub const INVALID: &str = "not valid TOML: fix it, or start over with `neosian configure --delete`";
// Held against provider_keys() by the keyless unit tier; registered Python
// doors stay in that process, while --env supplies any other door here.
pub const PROVIDERS: [(&str, &str); 7] = [
    ("openai", "OPENAI_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"),
    ("cerebras", "CEREBRAS_API_KEY"),
    ("xai", "XAI_API_KEY"),
    ("gemini", "GEMINI_API_KEY"),
    ("kimi", "MOONSHOT_API_KEY"),
    ("qwen", "DASHSCOPE_API_KEY"),
];

pub fn read(path: &Path) -> io::Result<toml::Table> {
    if !path.exists() {
        return Ok(toml::Table::new());
    }
    let text = fs::read_to_string(path)?;
    text.parse()
        .map_err(|_| io::Error::other(format!("{} is {INVALID}", path.display())))
}

pub fn write(path: &Path, document: &toml::Table) -> io::Result<()> {
    let parent = path.parent().expect("config under home");
    fileio::private_mkdir(parent)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(parent, fs::Permissions::from_mode(0o700))?;
    }
    let text = toml::to_string(document).map_err(io::Error::other)?;
    // The replacement is born private, tightening an existing file too.
    fileio::atomic_write(path, &text, false)
}

pub fn env_name(name: &str) -> bool {
    name.as_bytes().first().is_some_and(u8::is_ascii_uppercase)
        && name
            .bytes()
            .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == b'_')
}

pub fn providers(document: &toml::Table) -> Vec<(String, String)> {
    let mut rows: Vec<_> = PROVIDERS
        .iter()
        .map(|(n, e)| (n.to_string(), e.to_string()))
        .collect();
    if let Some(keys) = document.get("credentials").and_then(toml::Value::as_table) {
        for (key, value) in keys {
            let env = key.to_uppercase();
            if value.is_str() && env_name(&env) && !rows.iter().any(|(_, e)| *e == env) {
                rows.push((env.clone(), env));
            }
        }
    }
    rows
}

pub fn stored<'a>(document: &'a toml::Table, env: &str) -> Option<&'a str> {
    document
        .get("credentials")?
        .as_table()?
        .get(&env.to_lowercase())?
        .as_str()
}

pub fn rows(document: &toml::Table) -> Vec<Value> {
    providers(document)
        .into_iter()
        .map(|(name, env)| {
            let source = if std::env::var_os(&env).is_some_and(|v| !v.is_empty()) {
                Some("env")
            } else if stored(document, &env).is_some_and(|v| !v.is_empty()) {
                Some("file")
            } else {
                None
            };
            json!({"name": name, "env": env, "source": source})
        })
        .collect()
}
