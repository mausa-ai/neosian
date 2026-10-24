//! Standalone installers: the same writers as setup, with their own argv and artifacts.

use super::{
    context::{CLIENTS, Context, absolute, expand},
    hooks, install_output, mcp,
    settings::{self, Settings},
    targets,
};
use crate::{
    cli::usage,
    memory::mounts::{Mount, Refusal},
    repr::repr,
    store::grammar::Actor,
};
use clap::{Args, Subcommand};
use std::io::{self, Write};

#[derive(Args)]
pub struct Common {
    /// Apply the registration (default: print it).
    #[arg(long)]
    write: bool,
    /// One JSON envelope on stdout.
    #[arg(long)]
    json: bool,
    #[arg(long, default_value = "user", value_parser = ["user", "project"])]
    level: String,
    /// Register this binary instead of the running executable.
    #[arg(long)]
    binary: Option<String>,
    #[arg(long)]
    root: Option<String>,
    #[arg(long)]
    url: Option<String>,
    #[arg(long)]
    schema: Option<String>,
    #[arg(long)]
    profile: Option<String>,
    #[arg(long)]
    scope: Option<String>,
    #[arg(long, value_name = "scope=...,path=...[,ro|,eo]")]
    mount: Vec<String>,
}

#[derive(Args)]
pub struct McpInstall {
    #[command(flatten)]
    common: Common,
    #[arg(long, value_parser = ["claude-code", "claude-desktop", "codex", "opencode", "muse-code", "cursor", "pi"])]
    client: String,
    #[arg(long, default_value = "mcp:stdio")]
    actor: String,
}

#[derive(Args)]
pub struct RecordInstall {
    #[command(flatten)]
    common: Common,
    #[arg(long, value_parser = CLIENTS)]
    client: String,
    #[arg(long, default_value = "claude-code")]
    agent: String,
    #[arg(long)]
    spool: Option<String>,
}

#[derive(Subcommand)]
pub enum McpCommand {
    /// Print or apply an MCP client registration for neosian memory.
    Install(McpInstall),
}

#[derive(Subcommand)]
pub enum RecordCommand {
    /// Print or apply a foreign agent's hooks for neosian record.
    Install(RecordInstall),
}

impl Common {
    fn settings(&self, context: &Context, record: bool) -> Result<Settings, Refusal> {
        let mut resolved = settings::resolve_profile(
            self.root.as_deref(),
            self.url.as_deref(),
            self.profile.as_deref(),
            self.schema.is_some() || self.scope.is_some() || !self.mount.is_empty(),
            context,
        )
        .map_err(Refusal::Usage)?;
        if resolved.profile.is_some() {
            return Ok(resolved);
        }
        if let Some(schema) = &self.schema {
            if !resolved
                .credentials
                .iter()
                .any(|s| s == "NEOSIAN_POSTGRES_DSN")
            {
                return Err(Refusal::Usage(
                    "--schema applies only to Postgres (unset --root/--url)".into(),
                ));
            }
            if schema != "neosian" {
                resolved.schema = Some(schema.clone());
            }
        }
        let mounts = if self.scope.is_some() || !self.mount.is_empty() {
            if self.scope.is_some() && !self.mount.is_empty() {
                return Err(Refusal::Usage(
                    "--scope is the single-mount sugar; use --mount for multi-mount".into(),
                ));
            }
            if let Some(scope) = &self.scope {
                vec![Mount::new(scope, "memories", (false, false), "")?]
            } else {
                self.mount
                    .iter()
                    .map(|s| Mount::parse(s))
                    .collect::<Result<Vec<_>, _>>()?
            }
        } else {
            settings::mounts(&self.level, context)?
        };
        if mounts
            .iter()
            .any(|m| mounts.iter().filter(|other| m.path == other.path).count() > 1)
        {
            return Err(Refusal::Usage("mount paths must be unique".into()));
        }
        if record && !mounts.is_empty() && mounts.iter().all(|m| m.read_only || m.edit_only) {
            return Err(Refusal::Usage(
                "the sessions document needs a read-write mount (no ,ro or ,eo)".into(),
            ));
        }
        resolved.mounts = settings::mount_argv(&mounts);
        Ok(resolved)
    }
}

fn refused(
    error: Refusal,
    json: bool,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<u8> {
    match error {
        Refusal::Usage(message) => usage(json, &message, out, err),
        Refusal::Memory(error) => {
            writeln!(err, "error: [{}] {}", error.code, error.message)?;
            Ok(2)
        }
    }
}

pub fn mcp(args: &McpInstall, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let common = &args.common;
    let mut context = Context::new()?;
    if let Err(message) = context.binary(common.binary.as_deref()) {
        return usage(common.json, &message, out, err);
    }
    let target = targets::mcp(&args.client, &context, &common.level);
    if target.level != common.level {
        return usage(
            common.json,
            &format!(
                "--level {}: {} keeps one file for every project ({}); it registers at the user level",
                common.level,
                target.label,
                target.path.display()
            ),
            out,
            err,
        );
    }
    let mut settings = match common.settings(&context, false) {
        Ok(settings) => settings,
        Err(error) => return refused(error, common.json, out, err),
    };
    if let Err(reason) = Actor::parse(&args.actor) {
        return usage(
            common.json,
            &format!(
                "--actor {}: {reason} (e.g. cli:claude-code)",
                repr(&args.actor)
            ),
            out,
            err,
        );
    }
    if args.actor != "mcp:stdio" {
        settings.actor = Some(args.actor.clone());
    }
    let mut payload = mcp::install(&target, &context, &settings, common.write);
    if let Some(profile) = &settings.profile {
        payload["profile"] = profile.clone();
        payload["profile_support"] = settings::support(&args.client);
    }
    install_output::render(&payload, &target, &settings, false, common.json, out, err)
}

pub fn record(args: &RecordInstall, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let common = &args.common;
    let mut context = Context::new()?;
    if let Err(message) = context.binary(common.binary.as_deref()) {
        return usage(common.json, &message, out, err);
    }
    let mut settings = match common.settings(&context, true) {
        Ok(settings) => settings,
        Err(error) => return refused(error, common.json, out, err),
    };
    if let Err(reason) = Actor::parse(&format!("{}:session", args.agent)) {
        return usage(
            common.json,
            &format!(
                "--agent {}: {reason} (a kind, e.g. claude-code)",
                repr(&args.agent)
            ),
            out,
            err,
        );
    }
    if args.agent != "claude-code" {
        settings.agent = Some(args.agent.clone());
    }
    if let Some(spool) = &args.spool {
        if settings.profile.is_some() {
            return usage(
                common.json,
                "profile spools are managed under the neosian home; omit --spool",
                out,
                err,
            );
        }
        settings.spool = absolute(&context.cwd, &expand(spool, &context.home));
    }
    let target = targets::hooks(&args.client, &context, &common.level);
    let mut payload = hooks::install(&target, &context, &settings, common.write);
    if let Some(profile) = &settings.profile {
        payload["profile"] = profile.clone();
        payload["profile_support"] = settings::support(&args.client);
    }
    install_output::render(&payload, &target, &settings, true, common.json, out, err)
}
