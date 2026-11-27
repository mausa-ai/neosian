//! `setup`: wire the selected clients once per machine (§22.6, §30.3).

use super::{
    cache_install, codex_skills,
    context::{CLIENTS, Context},
    diagnostics, files, hooks, mcp, naming, receipt, rename, runner, settings, setup_check,
    setup_outcome::{Outcome, Ran},
    targets, write_key,
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
    /// Check saved registrations and probe this binary with temporary MCP/file storage.
    #[arg(long, conflicts_with = "write")]
    check: bool,
    /// Bring existing user-level registrations to this release; connect nothing new (update runs this).
    #[arg(long, conflicts_with_all = ["write", "check", "client", "level", "binary", "yes", "root", "url", "profile", "claude_cache", "codex_skills"])]
    refresh: bool,
    /// With --refresh: move every client still on neosian-memory to neosian, after listing what was found.
    #[arg(long, requires = "refresh")]
    rename: bool,
    /// Put every client on neosian back on neosian-memory, or the one named.
    #[arg(long, value_name = "CLIENT", num_args = 0..=1, default_missing_value = "all",
        conflicts_with_all = ["write", "check", "refresh"])]
    undo_rename: Option<String>,
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
    /// Install idle cache maintenance for Claude Code, or disable it and restore its prior statusline.
    #[arg(long, default_value = "auto", value_parser = ["auto", "off"])]
    claude_cache: String,
    /// Install Codex's upkeep skill, preserve an opt-out, explicitly enable it, or remove it.
    #[arg(long, default_value = "auto", value_parser = ["auto", "on", "off"])]
    codex_skills: String,
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

fn applied(mut envelope: Value) -> Value {
    if envelope.get("client").is_some() {
        envelope["applied"] = json!(envelope["written"] == true);
    }
    envelope
}

fn install_pair(args: &Setup, context: &Context, client: &str) -> Value {
    let target = targets::mcp(client, context, &args.level);
    let hook = targets::hooks(client, context, &args.level);
    // A move stopped between its steps is finished first, by the same rename
    // the refresh runs; setup never writes the old key beside our new one.
    if args.write && !rename::servers::two(rename::static_client(client), context, true).is_empty()
    {
        let _ = rename::run(context, true, Some(client));
        let (pairs, lines) = rename::report::two(rename::static_client(client), context, true);
        if let Some(line) = lines.first() {
            let hand = pairs.first().is_some_and(|p| p["hand"] == true);
            let fix = if hand {
                "two_fix_setup_hand"
            } else {
                "two_fix_setup"
            };
            let failure = files::Failure::new(line.clone(), rename::text::get(fix));
            return json!({"client": client, "label": target.label, "mcp": failure.payload(&target),
                "hooks": failure.payload(&hook)});
        }
    }
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
    if client == "codex" && args.level == "user" {
        let skills = codex_skills::install(context, &args.codex_skills, false);
        if skills["success"] != true {
            return json!({"client":client,"label":target.label,
                "mcp":applied(mcp::install(&target, context, &settings, false)),
                "hooks":applied(hooks::install(&hook, context, &settings, false)),"skills":skills});
        }
    }
    if args.write && ["claude-code", "codex", "muse-code", "cursor", "pi"].contains(&client) {
        let mcp = applied(match &incompatible {
            Some(message) => grammar(message.clone()),
            None => mcp::install(&target, context, &settings, false),
        });
        let hooks = applied(hooks::install(&hook, context, &settings, false));
        if mcp["success"] != true || hooks["success"] != true {
            return json!({"client": client, "label": target.label, "mcp": mcp, "hooks": hooks});
        }
        if client == "claude-code" && args.claude_cache == "auto" {
            let cache = cache_install::install(context, &args.level, false);
            if cache["success"] != true {
                return json!({"client":client,"label":target.label,"mcp":mcp,"hooks":hooks,"cache":cache});
            }
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
        let key = mcp["server_name"]
            .as_str()
            .unwrap_or(naming::NEW)
            .to_owned();
        let argv = mcp::apply_argv(&target, &mcp["entry"], &key);
        let mut result = runner::run(&argv, &context.env);
        // Remove and retry only an entry the ownership rule knows as ours.
        if result.as_ref().is_some_and(|(ok, _)| !ok) && write_key::ours_at(&target, &key) {
            let mut remove = vec![argv[0].clone(), "mcp".into(), "remove".into()];
            if target.cli == Some("claude") {
                remove.extend(["--scope".into(), "user".into()]);
            }
            remove.push(key.clone());
            runner::run(&remove, &context.env);
            result = runner::run(&argv, &context.env);
        }
        // A `neosian` the CLI wrote is recorded as ours, read back as it stands.
        if result.as_ref().is_some_and(|(ok, _)| *ok) && key == naming::NEW {
            let pointer = targets::pointer(&target, naming::NEW);
            let written = targets::document(client, &target.path)
                .value()
                .and_then(|d| d.pointer(&pointer).cloned());
            if let Some(written) = written
                && let Err(failure) =
                    receipt::record(client, context, &target.path, &pointer, &written)
            {
                result = Some((false, failure.message));
            }
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
    if client == "codex" && args.level == "user" {
        let ready = row["mcp"]["success"] == true
            && row["mcp"]["applied"] == true
            && row["hooks"]["success"] == true
            && row["hooks"]["applied"] == true;
        row["skills"] = codex_skills::install(context, &args.codex_skills, args.write && ready);
    }
    if client == "claude-code" {
        row["cache"] = if args.claude_cache == "off" {
            cache_install::disable(context, &args.level, args.write)
        } else {
            cache_install::install(context, &args.level, args.write)
        };
    }
    if let Some(profile) = settings.profile {
        row["profile"] = profile;
        row["profile_support"] = settings::support(client);
    }
    row
}

pub fn run(args: &Setup, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let ran = apply(args, out, err)?;
    let url = args.url.as_ref().is_some_and(|value| !value.is_empty());
    super::setup_text::report(ran, args.json, url, out, err)
}

impl Setup {
    /// What `neosian setup --write` parses to: login connects agents with it.
    pub fn login() -> Self {
        use clap::{Args, Command, FromArgMatches};
        let command = Self::augment_args(Command::new("setup"));
        command
            .try_get_matches_from(["setup", "--write"])
            .and_then(|matches| Self::from_arg_matches(&matches))
            .expect("setup --write parses")
    }
}

/// Select and write the clients; the checklist is drawn on `out`.
pub fn apply(args: &Setup, out: &mut impl Write, err: &mut impl Write) -> io::Result<Ran> {
    let mut context = Context::new()?;
    if let Err(message) = context
        .locations(&args.at)
        .and_then(|()| context.binary(args.binary.as_deref()))
    {
        return crate::cli::usage(args.json, &message, out, err).map(Ran::Exit);
    }
    if args.refresh {
        return super::refresh::run(&context, args.rename, args.json, out).map(Ran::Exit);
    }
    if let Some(which) = &args.undo_rename {
        if which != "all" && !naming::renamed(which) {
            let message = format!("--undo-rename takes one of {}", naming::CLIENTS.join(", "));
            return crate::cli::usage(args.json, &message, out, err).map(Ran::Exit);
        }
        let only = (which != "all").then_some(which.as_str());
        return rename::undo::run(&context, only, args.json, out).map(Ran::Exit);
    }
    let login_home = crate::profile::files::home().map_err(io::Error::other)?;
    if let Some(login) = crate::login::local::state(&login_home).map_err(io::Error::other)?
        && crate::login::local::status(&login_home, &login).map_err(io::Error::other)?["expires_soon"]
            == true
    {
        writeln!(err, "This credential expires soon; run neosian login.")?;
    }
    let tty = io::stdin().is_terminal() && io::stdout().is_terminal();
    if args.write && args.client.is_empty() && !args.yes && (args.json || !tty) {
        return crate::cli::usage(
            args.json,
            "unattended --write requires --yes or --client",
            out,
            err,
        )
        .map(Ran::Exit);
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
    let installation = diagnostics::installation(&context);
    let storage = diagnostics::selection(
        args.root.as_deref(),
        args.url.as_deref(),
        args.profile.as_deref(),
        &context,
    );
    if args.check {
        return setup_check::run(
            &context,
            &clients,
            storage,
            args.root.is_some() || args.url.is_some() || args.profile.is_some(),
            args.json,
            out,
        )
        .map(Ran::Exit);
    }
    if clients.is_empty() {
        return Ok(Ran::NoClient(searched));
    }
    if args.write && args.client.is_empty() && !args.yes {
        let labels: Vec<_> = clients
            .iter()
            .map(|c| targets::mcp(c, &context, "user").label)
            .collect();
        let Some(selected) = super::selection::select(&labels, &mut io::stdin().lock(), out)?
        else {
            return Ok(Ran::Cancelled);
        };
        clients = clients
            .into_iter()
            .enumerate()
            .filter_map(|(i, c)| selected.contains(&i).then_some(c))
            .collect();
        if clients.is_empty() {
            return Ok(Ran::NoneSelected);
        }
    }

    let rows: Vec<_> = clients
        .iter()
        .map(|client| install_pair(args, &context, client))
        .collect();
    let failed = rows.iter().any(|row| {
        ["mcp", "hooks", "cache", "skills"].iter().any(|k| {
            !row[k].is_null()
                && (row[k]["success"] != true || (args.write && row[k]["applied"] != true))
        })
    });
    Ok(Ran::Done(Outcome {
        write: args.write,
        level: args.level.clone(),
        rows,
        searched,
        installation,
        storage,
        failed,
    }))
}
