//! `status`: read-only findings across both registration levels (§30.1).

use super::{
    context::{CLIENTS, Context},
    hooks, runner, targets,
};
use crate::{config, home, memory::mounts::Refusal, store::FileStore, version};
use clap::Args;
use serde_json::{Value, json};
use std::io::{self, Write};
use std::path::Path;

#[derive(Args)]
pub struct Status {
    #[arg(long)]
    json: bool,
    #[arg(long, value_name = "CLIENT=DIR")]
    at: Vec<String>,
}

type Found = Vec<(String, String, Vec<String>)>;

fn at<'a>(found: &'a Found, level: &str) -> Option<&'a Vec<String>> {
    found.iter().find(|(_, l, _)| l == level).map(|(_, _, a)| a)
}

fn root(argv: Option<&Vec<String>>) -> Option<&str> {
    let argv = argv?;
    argv.iter()
        .position(|s| s == "--root")
        .and_then(|i| argv.get(i + 1))
        .map(String::as_str)
}

fn client_status(client: &str, context: &Context) -> Value {
    let mut servers: Found = vec![];
    let mut hooks: Found = vec![];
    for level in ["user", "project"] {
        let target = targets::mcp(client, context, level);
        let path = target.path.display().to_string();
        if let Some(argv) = targets::registered(&target)
            && !servers.iter().any(|(p, _, _)| *p == path)
        {
            servers.push((path, target.level, argv));
        }
        for target in targets::active(client, context, level) {
            let path = target.path.display().to_string();
            if let Some(argv) = hooks::installed(&target)
                && !hooks.iter().any(|(p, _, _)| *p == path)
            {
                hooks.push((path, target.level, argv));
            }
        }
    }
    let server = at(&servers, "project").or_else(|| at(&servers, "user"));
    let hook = at(&hooks, "user").or_else(|| at(&hooks, "project"));
    let interpreter = [server, hook].into_iter().flatten().find_map(|a| a.first());
    let resolves = interpreter.map(|command| {
        if Path::new(command).is_absolute() {
            Path::new(command).is_file()
        } else {
            runner::which(command, &context.env).is_some()
        }
    });
    let user = servers.iter().chain(&hooks).any(|(_, l, _)| l == "user");
    let project = servers.iter().chain(&hooks).any(|(_, l, _)| l == "project");
    let level = match (user, project) {
        (true, true) => Some("both"),
        (true, false) => Some("user"),
        (false, true) => Some("project"),
        _ => None,
    };
    let root = if root(server) == root(hook) {
        root(server)
    } else {
        None
    };
    let delivery = if ["pi", "opencode"].contains(&client)
        && hooks
            .iter()
            .any(|(p, _, _)| std::fs::read_to_string(p).is_ok_and(|s| s.contains("MailboxPoll")))
    {
        vec!["next_activity", "wake"]
    } else if hook.is_some() {
        vec!["next_activity"]
    } else {
        vec![]
    };
    let shadow = if client == "muse-code" && at(&servers, "user").is_some() {
        servers
            .iter()
            .find(|(_, l, _)| l == "project")
            .map(|(p, _, _)| p)
    } else {
        None
    };
    let target = targets::mcp(client, context, "user");
    json!({"client": client, "label": target.label, "installed": target.evidence.is_dir(),
        "mcp_registered": server.is_some(), "hooks_present": hook.is_some(), "level": level,
        "hook_files": hooks.iter().map(|(p, _, _)| p).collect::<Vec<_>>(), "interpreter": interpreter,
        "interpreter_resolves": resolves, "mcp_shadowed_by": shadow, "root": root,
        "message_delivery": delivery, "searched_directory": target.evidence})
}

fn last_session(root: &Path, scope: &str) -> Option<Value> {
    if !root.is_dir() {
        return None;
    }
    let store = FileStore::open(root).ok()?;
    let entries = store.list_documents(scope, "sessions/").ok()?;
    let newest = entries
        .iter()
        .rev()
        .filter(|e| !e.redacted)
        .max_by_key(|e| e.updated_at.instant())?;
    let document = store.read(scope, &newest.path).ok()??;
    let agent = document
        .content
        .lines()
        .filter_map(|s| s.strip_prefix("- agent: "))
        .next_back()
        .unwrap_or("-")
        .trim();
    Some(
        json!({"conversation": newest.path.trim_start_matches("sessions/"), "agent": agent,
        "updated_at": newest.updated_at.z(), "path": newest.path}),
    )
}

pub fn run(args: &Status, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let mut context = Context::new()?;
    if let Err(message) = context.locations(&args.at) {
        return crate::cli::usage(args.json, &message, out, err);
    }
    let root = home::home()?;
    let path = root.join("config.toml");
    let mut scopes = Value::Null;
    let mut scopes_error = Value::Null;
    let mut session = None;
    match home::project_mounts(&context.cwd) {
        Ok(mounts) => {
            scopes = json!({});
            for mount in &mounts {
                scopes[format!("/{}", mount.path)] = json!(mount.scope);
            }
            session = last_session(&root, &mounts[1].scope);
        }
        Err(error) => {
            scopes_error = json!(match error {
                Refusal::Usage(s) => s,
                Refusal::Memory(e) => e.message,
            })
        }
    }
    let loaded = config::read(&path);
    let config_error = loaded.as_ref().err().map(|e| {
        e.to_string()
            .strip_prefix(&format!("{} is ", path.display()))
            .unwrap_or(&e.to_string())
            .to_string()
    });
    let document = loaded.unwrap_or_default();
    let mode = document
        .get("update")
        .and_then(toml::Value::as_table)
        .and_then(|t| t.get("mode"))
        .map(|v| crate::pyfmt::py_str(&serde_json::to_value(v).unwrap()))
        .unwrap_or("off".into());
    let clients: Vec<_> = CLIENTS
        .iter()
        .map(|client| client_status(client, &context))
        .collect();
    let double_fire: Vec<_> = clients.iter().filter_map(|c| {
        let files = c["hook_files"].as_array().unwrap();
        (files.len() >= 2).then(|| format!("{}: the hooks are installed in multiple sources ({} and {}), and each span can land more than once. Run `neosian setup --write` here to keep the one per machine", c["client"].as_str().unwrap(), files[0].as_str().unwrap(), files[1].as_str().unwrap()))
    }).collect();
    let status = json!({"version": version::VERSION, "home": root, "home_exists": root.is_dir(),
        "config_path": path, "config_exists": path.is_file(), "config_error": config_error,
        "providers": config::rows(&document), "scopes": scopes, "scopes_error": scopes_error,
        "clients": clients, "last_session": session, "one_writer": [], "double_fire": double_fire,
        "shape": "binary", "upgrade": "neosian-update", "update_mode": mode});
    if args.json {
        writeln!(out, "{status}")?;
    } else {
        render(&status, out)?;
    }
    Ok(0)
}

fn string<'a>(value: &'a Value, key: &str) -> &'a str {
    value[key].as_str().unwrap_or("-")
}

fn render(status: &Value, out: &mut impl Write) -> io::Result<()> {
    writeln!(
        out,
        "neosian {}  binary  upgrade: neosian-update",
        version::VERSION
    )?;
    let exists = |key| {
        if status[key] == true {
            "exists"
        } else {
            "missing"
        }
    };
    writeln!(
        out,
        "home      {}  ({})",
        string(status, "home"),
        exists("home_exists")
    )?;
    writeln!(
        out,
        "config    {}  ({})",
        string(status, "config_path"),
        status["config_error"]
            .as_str()
            .unwrap_or(exists("config_exists"))
    )?;
    let keyed: Vec<_> = status["providers"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|p| p["source"].is_string())
        .map(|p| format!("{} ({})", string(p, "name"), string(p, "source")))
        .collect();
    writeln!(
        out,
        "keys      {}",
        if keyed.is_empty() {
            "none — neosian configure".into()
        } else {
            keyed.join(", ")
        }
    )?;
    let scopes = status["scopes"]
        .as_object()
        .map(|s| {
            s.iter()
                .map(|(k, v)| format!("{k} = {}", v.as_str().unwrap()))
                .collect::<Vec<_>>()
                .join("  ")
        })
        .unwrap_or_else(|| string(status, "scopes_error").into());
    writeln!(out, "scopes    {scopes}\nclients")?;
    for client in status["clients"].as_array().unwrap() {
        let name = string(client, "client");
        if client["installed"] != true {
            writeln!(
                out,
                "  {name:<12}not installed; searched {}",
                string(client, "searched_directory")
            )?;
            continue;
        }
        let mut cells = vec![
            if client["mcp_registered"] == true {
                "mcp registered".into()
            } else {
                "mcp -".into()
            },
            if client["hooks_present"] == true {
                "hooks present".into()
            } else {
                "hooks -".into()
            },
            format!("level {}", string(client, "level")),
        ];
        if client["mcp_shadowed_by"].is_string() {
            cells.push(format!(
                "user MCP overridden by shared {}",
                string(client, "mcp_shadowed_by")
            ));
        }
        let delivery = client["message_delivery"].as_array().unwrap();
        if !delivery.is_empty() {
            cells.push(format!(
                "messages {}",
                delivery
                    .iter()
                    .map(|v| v.as_str().unwrap())
                    .collect::<Vec<_>>()
                    .join(", ")
            ));
        }
        match client["interpreter_resolves"].as_bool() {
            Some(true) => cells.push("interpreter ok".into()),
            Some(false) => cells.push(format!(
                "interpreter missing: {}",
                string(client, "interpreter")
            )),
            _ => {}
        }
        writeln!(out, "  {name:<12}installed  {}", cells.join("  "))?;
    }
    let s = &status["last_session"];
    if s.is_object() {
        writeln!(
            out,
            "session   {} {}  {}  (/project/{})",
            string(s, "agent"),
            string(s, "conversation"),
            string(s, "updated_at"),
            string(s, "path")
        )?;
    } else {
        writeln!(out, "session   none recorded")?;
    }
    for note in status["double_fire"].as_array().unwrap() {
        writeln!(out, "note      {}", note.as_str().unwrap())?;
    }
    writeln!(out, "update    mode {}", string(status, "update_mode"))
}
