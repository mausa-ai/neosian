//! `setup`: wire the selected clients once per machine (§22.6, §30.3).

use super::{
    context::{CLIENTS, Context},
    files, hooks, mcp, runner, settings, targets,
};
use clap::Args;
use serde_json::{Value, json};
use std::io::{self, IsTerminal, Write};

#[derive(Args)]
pub struct Setup {
    #[arg(long, value_parser = CLIENTS)]
    client: Vec<String>,
    #[arg(long)]
    write: bool,
    #[arg(long)]
    json: bool,
    #[arg(long, default_value = "user", value_parser = ["user", "project"])]
    pub level: String,
    #[arg(long)]
    binary: Option<String>,
    #[arg(long, value_name = "CLIENT=DIR")]
    at: Vec<String>,
    #[arg(long)]
    yes: bool,
    #[arg(long)]
    pub root: Option<String>,
    #[arg(long)]
    pub url: Option<String>,
    #[arg(long)]
    pub profile: Option<String>,
}

fn discovery(context: &Context, clients: &[String]) -> Vec<Value> {
    clients
        .iter()
        .map(|client| {
            let directory = context.directory(client);
            json!({"client": client, "directory": directory, "installed": directory.is_dir()})
        })
        .collect()
}

fn discovery_text(rows: &[Value]) -> String {
    rows.iter()
        .filter(|r| r["installed"] == false)
        .map(|r| {
            format!(
                "  {}: not found; searched {}\n",
                text(r, "client"),
                text(r, "directory")
            )
        })
        .collect()
}

fn text<'a>(value: &'a Value, key: &str) -> &'a str {
    value[key].as_str().unwrap_or("-")
}

fn applied(mut envelope: Value) -> Value {
    if envelope.get("client").is_some() {
        envelope["applied"] = json!(envelope["written"] == true);
    }
    envelope
}

fn install_pair(args: &Setup, context: &Context, client: &str) -> Value {
    let target = targets::mcp(client, context, &args.level);
    let hook = targets::hooks(client, context, &args.level);
    let settings = settings::resolve_profile(
        args.root.as_deref().filter(|v| !v.is_empty()),
        args.url.as_deref().filter(|v| !v.is_empty()),
        args.profile.as_deref(),
        false,
        context,
    )
    .and_then(|mut settings| {
        if settings.profile.is_none() {
            settings.mounts = settings::mount_argv(
                &settings::mounts(&args.level, context).map_err(settings::refusal)?,
            );
        }
        Ok(settings)
    });
    let grammar = |message: String| json!({"success": false, "error": message, "hint": null});
    let incompatible = (target.level != args.level).then(|| {
        format!(
            "--level {}: {} keeps one file for every project ({}); it registers at the user level",
            args.level,
            target.label,
            target.path.display()
        )
    });
    let settings = match settings {
        Ok(s) => s,
        Err(message) => {
            return json!({"client": client, "label": target.label,
            "mcp": grammar(incompatible.unwrap_or_else(|| message.clone())), "hooks": grammar(message)});
        }
    };
    if args.write && ["muse-code", "cursor", "pi"].contains(&client) {
        let mcp = applied(match &incompatible {
            Some(message) => grammar(message.clone()),
            None => mcp::install(&target, context, &settings, false),
        });
        let hooks = applied(hooks::install(&hook, context, &settings, false));
        if mcp["success"] != true || hooks["success"] != true {
            return json!({"client": client, "label": target.label, "mcp": mcp, "hooks": hooks});
        }
    }
    let mut mcp = applied(match incompatible {
        Some(message) => grammar(message),
        None => mcp::install(
            &target,
            context,
            &settings,
            args.write && target.cli.is_none(),
        ),
    });
    if args.write && mcp["apply"].is_string() {
        let argv = mcp::apply_argv(&target, &mcp["entry"]);
        let mut result = runner::run(&argv, &context.env);
        if result.as_ref().is_some_and(|(ok, _)| !ok) {
            let mut remove = vec![argv[0].clone(), "mcp".into(), "remove".into()];
            if target.cli == Some("claude") {
                remove.extend(["--scope".into(), "user".into()]);
            }
            remove.push(targets::SERVER.into());
            runner::run(&remove, &context.env);
            result = runner::run(&argv, &context.env);
        }
        mcp["applied"] = json!(result.as_ref().is_some_and(|(ok, _)| *ok));
        mcp["apply_error"] = result
            .filter(|(ok, _)| !ok)
            .map_or(Value::Null, |(_, err)| json!(err));
        let project = targets::mcp(client, context, "project");
        if mcp["applied"] == true
            && project.path != target.path
            && let Err(e) = mcp::displace(&project, true)
        {
            mcp["apply_error"] = json!(e.message);
        }
    }
    let hooks = applied(hooks::install(&hook, context, &settings, args.write));
    let mut row = json!({"client": client, "label": target.label, "mcp": mcp, "hooks": hooks});
    if let Some(profile) = settings.profile {
        row["profile"] = profile;
        row["profile_support"] = settings::support(client);
    }
    row
}

fn line(kind: &str, envelope: &Value, write: bool) -> String {
    let prefix = format!("  {kind:<6}{}  ", text(envelope, "config_path"));
    let result = if envelope["success"] != true {
        format!("refused: {}", text(envelope, "error"))
    } else if let Some(apply) = envelope["apply"].as_str() {
        if !write {
            format!("would run: {apply}")
        } else if envelope["applied"] == true {
            format!("applied: {apply}")
        } else {
            let why = envelope["apply_error"]
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| format!("{} is not on PATH", shlex::split(apply).unwrap()[0]));
            format!("not applied ({why}); run: {apply}")
        }
    } else if write {
        if envelope["applied"] != true {
            "not applied (preflight failed)".into()
        } else if envelope["created"] == true {
            "created".into()
        } else {
            "updated".into()
        }
    } else {
        "would write".into()
    };
    prefix + &result
}

pub fn run(args: &Setup, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let mut context = Context::new()?;
    if let Err(message) = context
        .locations(&args.at)
        .and_then(|()| context.binary(args.binary.as_deref()))
    {
        return crate::cli::usage(args.json, &message, out, err);
    }
    let tty = io::stdin().is_terminal() && io::stdout().is_terminal();
    if args.write && args.client.is_empty() && !args.yes && (args.json || !tty) {
        return crate::cli::usage(
            args.json,
            "unattended --write requires --yes or --client",
            out,
            err,
        );
    }
    let searched_clients = if args.client.is_empty() {
        CLIENTS.map(str::to_owned).to_vec()
    } else {
        args.client.clone()
    };
    let searched = discovery(&context, &searched_clients);
    let mut clients = Vec::new();
    for client in &searched_clients {
        if !clients.contains(client)
            && (!args.client.is_empty() || context.directory(client).is_dir())
        {
            clients.push(client.clone());
        }
    }
    if clients.is_empty() {
        let message = format!(
            "no client found: none of {} is installed",
            CLIENTS.join(", ")
        );
        if args.json {
            writeln!(
                out,
                "{}",
                json!({"error": message, "hint": null, "searched": searched})
            )?;
        }
        writeln!(err, "error: {message}")?;
        write!(err, "{}", discovery_text(&searched))?;
        return Ok(1);
    }
    if args.write && args.client.is_empty() && !args.yes {
        let labels: Vec<_> = clients
            .iter()
            .map(|c| targets::mcp(c, &context, "user").label)
            .collect();
        let Some(selected) = super::selection::select(&labels, &mut io::stdin().lock(), out)?
        else {
            writeln!(err, "setup cancelled; nothing written")?;
            return Ok(1);
        };
        clients = clients
            .into_iter()
            .enumerate()
            .filter_map(|(i, c)| selected.contains(&i).then_some(c))
            .collect();
        if clients.is_empty() {
            writeln!(out, "No clients selected; nothing written.")?;
            return Ok(0);
        }
    }

    let rows: Vec<_> = clients
        .iter()
        .map(|client| install_pair(args, &context, client))
        .collect();
    let failed = rows.iter().any(|row| {
        ["mcp", "hooks"]
            .iter()
            .any(|k| row[k]["success"] != true || (args.write && row[k]["applied"] != true))
    });
    if args.json {
        writeln!(
            out,
            "{}",
            json!({"written": args.write, "level": args.level, "clients": rows, "searched": searched})
        )?;
    } else {
        for row in &rows {
            writeln!(out, "{}", text(row, "label"))?;
            if row["profile"].is_object() {
                writeln!(
                    out,
                    "  profile {}  capture {}  context {}",
                    text(&row["profile"], "profile"),
                    text(&row["profile"], "capture"),
                    text(&row["profile_support"], "context")
                )?;
                writeln!(
                    out,
                    "  capture fields {}  history source {}  support {}",
                    row["profile"]["definition"]["capture"]["fields"],
                    text(&row["profile"]["definition"]["history"], "source"),
                    text(&row["profile_support"], "capture")
                )?;
            }
            for kind in ["mcp", "hooks"] {
                writeln!(out, "{}", line(kind, &row[kind], args.write))?;
            }
            for note in [&row["mcp"]["note"], &row["hooks"]["trust_hint"]]
                .into_iter()
                .filter_map(Value::as_str)
            {
                writeln!(out, "  note  {note}")?;
            }
            if let Some(shadow) = row["mcp"]["mcp_shadowed_by"].as_str() {
                writeln!(
                    err,
                    "note: preserved shared {shadow}; it overrides Muse's user registration"
                )?;
            }
            if let Some(files) = row["hooks"].get("files")
                && !args.write
            {
                write!(out, "{}", files::pretty(files))?;
            }
        }
        if !args.write {
            writeln!(err, "hint: re-run with --write to apply")?;
        }
        write!(out, "{}", discovery_text(&searched))?;
    }
    if args.url.as_ref().is_some_and(|value| !value.is_empty()) {
        writeln!(
            err,
            "hint: set NEOSIAN_CLIENT_TOKEN in each client's own environment: the token is never written into a registration or a hook line"
        )?;
    }
    Ok(u8::from(failed))
}
