//! Stdin-only credential writes and value-blind listings (§30.1).

use crate::{config, home, repr::repr};
use clap::Args;
use serde_json::{Value, json};
use std::io::{self, IsTerminal, Read, Write};

#[derive(Args)]
pub struct Configure {
    #[arg(long)]
    provider: Option<String>,
    #[arg(long)]
    env: Option<String>,
    #[arg(long)]
    key: Option<String>,
    #[arg(long)]
    delete: bool,
    #[arg(long)]
    list: bool,
    #[arg(long)]
    json: bool,
}

fn selected(args: &Configure, document: &toml::Table) -> Result<Option<(String, String)>, String> {
    if args.provider.is_some() && args.env.is_some() {
        return Err("--provider and --env name the same thing: pass one".into());
    }
    let providers = config::providers(document);
    let mut row = None;
    if let Some(provider) = &args.provider {
        row = providers.iter().find(|(name, _)| name == provider).cloned();
        if row.is_none() {
            return Err(format!(
                "unknown provider {}; known: {}",
                repr(provider),
                providers
                    .iter()
                    .map(|(n, _)| n.as_str())
                    .collect::<Vec<_>>()
                    .join(", ")
            ));
        }
    }
    if let Some(env) = &args.env {
        if !config::env_name(env) {
            return Err(format!(
                "--env {}: an environment variable name (uppercase letters, digits, '_')",
                repr(env)
            ));
        }
        row = Some(
            providers
                .iter()
                .find(|(_, e)| e == env)
                .cloned()
                .unwrap_or((env.clone(), env.clone())),
        );
    }
    if args.key.as_ref().is_some_and(|v| v != "-") {
        return Err("a key is read from stdin: pass --key - and pipe the value".into());
    }
    if args.key.is_some() && row.is_none() {
        return Err("--key needs --provider or --env".into());
    }
    if row.is_some() && args.key.is_none() && !args.delete {
        return Err("--provider and --env need --key - or --delete".into());
    }
    Ok(row)
}

fn emit(json: bool, payload: Value, line: &str, out: &mut impl Write) -> io::Result<u8> {
    if json {
        writeln!(out, "{payload}")?;
    } else {
        writeln!(out, "{line}")?;
    }
    Ok(0)
}

fn save(document: &mut toml::Table, env: &str, value: String) {
    if !document
        .get("credentials")
        .is_some_and(toml::Value::is_table)
    {
        document.insert("credentials".into(), toml::Value::Table(toml::Table::new()));
    }
    document
        .get_mut("credentials")
        .unwrap()
        .as_table_mut()
        .unwrap()
        .insert(env.to_lowercase(), toml::Value::String(value));
}

pub fn run(args: &Configure, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let path = home::home()?.join("config.toml");
    let loaded = config::read(&path);
    let empty = toml::Table::new();
    let row = match selected(args, loaded.as_ref().unwrap_or(&empty)) {
        Ok(row) => row,
        Err(message) => return crate::cli::usage(args.json, &message, out, err),
    };
    if args.delete && row.is_none() {
        let existed = path.exists();
        if existed {
            std::fs::remove_file(&path)?;
        }
        return emit(
            args.json,
            json!({"deleted": path, "existed": existed}),
            &format!(
                "{} {}",
                if existed { "deleted" } else { "no config at" },
                path.display()
            ),
            out,
        );
    }
    let mut document = match loaded {
        Ok(document) => document,
        Err(error) => {
            if args.json {
                writeln!(out, "{}", json!({"error": error.to_string(), "hint": null}))?;
            }
            writeln!(err, "error: {error}")?;
            return Ok(1);
        }
    };
    if let Some((name, env)) = row {
        if args.delete {
            let existed = document
                .get_mut("credentials")
                .and_then(toml::Value::as_table_mut)
                .is_some_and(|keys| keys.remove(&env.to_lowercase()).is_some());
            if existed {
                config::write(&path, &document)?;
            }
            return emit(
                args.json,
                json!({"deleted": name, "existed": existed}),
                &format!("{} {name}", if existed { "deleted" } else { "no key for" }),
                out,
            );
        }
        let mut value = String::new();
        io::stdin().read_to_string(&mut value)?;
        let value = crate::text::strip(&value).to_string();
        if value.is_empty() {
            writeln!(err, "error: empty key on stdin")?;
            return Ok(1);
        }
        save(&mut document, &env, value);
        config::write(&path, &document)?;
        return emit(
            args.json,
            json!({"saved": name, "env": env, "config_path": path}),
            &format!("saved {name} ({env}) in {}", path.display()),
            out,
        );
    }
    if args.list || args.json || !io::stdin().is_terminal() || !io::stdout().is_terminal() {
        let rows = config::rows(&document);
        if args.json {
            writeln!(out, "{}", json!({"config_path": path, "providers": rows}))?;
        } else {
            let width = rows
                .iter()
                .map(|r| r["name"].as_str().unwrap().len())
                .max()
                .unwrap_or(0);
            for row in rows {
                writeln!(
                    out,
                    "{:<width$}  {}  {}",
                    row["name"].as_str().unwrap(),
                    row["env"].as_str().unwrap(),
                    row["source"].as_str().unwrap_or("-")
                )?;
            }
            writeln!(out, "config: {}", path.display())?;
        }
        return Ok(0);
    }
    writeln!(
        out,
        "Enter keeps an existing key; a key is stored under the home."
    )?;
    for (name, env) in config::providers(&document) {
        // The prompt echoes no credential, including an existing one.
        write!(out, "{name} ({env}): ")?;
        out.flush()?;
        let value = console::Term::stdout().read_secure_line()?;
        if !value.is_empty() {
            save(&mut document, &env, value);
            config::write(&path, &document)?;
            writeln!(out, "saved {name}")?;
        }
    }
    writeln!(out, "config: {}", path.display())?;
    Ok(0)
}
