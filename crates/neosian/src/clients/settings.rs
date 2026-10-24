//! Registration argv: store selection and a per-project layout, without opening a store.

use super::context::{Context, absolute, expand};
use crate::{
    home,
    memory::mounts::{Mount, Refusal},
    repr::repr,
};
use std::path::PathBuf;

pub struct Settings {
    pub store: Vec<String>,
    pub mounts: Vec<String>,
    pub spool: PathBuf,
    pub credentials: Vec<String>,
    pub profile: Option<serde_json::Value>,
    pub schema: Option<String>,
    pub actor: Option<String>,
    pub agent: Option<String>,
}

pub fn refusal(error: Refusal) -> String {
    match error {
        Refusal::Usage(s) => s,
        Refusal::Memory(e) => format!("error: [{}] {}", e.code, e.message),
    }
}

pub fn resolve(
    root_arg: Option<&str>,
    url_arg: Option<&str>,
    context: &Context,
) -> Result<Settings, String> {
    let dsn = context
        .env
        .get("NEOSIAN_POSTGRES_DSN")
        .is_some_and(|v| !v.is_empty());
    let named: Vec<_> = [
        ("--root", root_arg.is_some()),
        ("NEOSIAN_POSTGRES_DSN", dsn),
        ("--url", url_arg.is_some()),
    ]
    .into_iter()
    .filter_map(|(name, set)| set.then_some(name))
    .collect();
    if named.len() > 1 {
        return Err(format!("{} are mutually exclusive", named.join(" and ")));
    }
    let root = home::home().map_err(|e| e.to_string())?;
    let mut settings = Settings {
        store: vec![],
        mounts: vec![],
        spool: absolute(&context.cwd, &root.join("spool")),
        credentials: vec![],
        profile: None,
        schema: None,
        actor: None,
        agent: None,
    };
    if let Some(url) = url_arg {
        if !url.starts_with("http://") && !url.starts_with("https://") {
            return Err(format!(
                "--url must start with http:// or https://, got {}",
                repr(url)
            ));
        }
        if !context
            .env
            .get("NEOSIAN_CLIENT_TOKEN")
            .is_some_and(|v| !v.is_empty())
        {
            return Err("--url needs NEOSIAN_CLIENT_TOKEN — the client's bearer token, never on the command line".into());
        }
        settings.store = vec!["--url".into(), url.into()];
        settings.credentials.push("NEOSIAN_CLIENT_TOKEN".into());
    } else if dsn {
        settings.credentials.push("NEOSIAN_POSTGRES_DSN".into());
    } else {
        let root = root_arg.map_or(root, |s| expand(s, &context.home));
        settings.store = vec![
            "--root".into(),
            absolute(&context.cwd, &root).display().to_string(),
        ];
    }
    Ok(settings)
}

pub fn resolve_profile(
    root: Option<&str>,
    url: Option<&str>,
    name: Option<&str>,
    legacy: bool,
    context: &Context,
) -> Result<Settings, String> {
    let Some(selected) = crate::profile::resolve::selected(name, &context.cwd)? else {
        return resolve(root, url, context);
    };
    selected.conflicts(root.is_some() || url.is_some() || legacy)?;
    selected.check()?;
    let mut credentials = Vec::new();
    for source in &selected.profile.sources {
        if let Some(name) = selected.bindings[source].credential()
            && !credentials.contains(&name.to_string())
        {
            credentials.push(name.to_string());
        }
    }
    if context
        .env
        .get("NEOSIAN_HOME")
        .is_some_and(|s| !s.is_empty())
    {
        credentials.push("NEOSIAN_HOME".into());
    }
    Ok(Settings {
        store: vec!["--profile".into(), selected.name.clone()],
        mounts: vec![],
        spool: selected.home.join("profile-spool"),
        credentials,
        profile: Some(selected.report()),
        schema: None,
        actor: None,
        agent: None,
    })
}

pub fn support(client: &str) -> serde_json::Value {
    let context = match client {
        "opencode" => "mcp_instructions",
        "pi" => "system_prompt_section",
        "claude-desktop" => "client_dependent_mcp_instructions",
        _ => "session_start_hook",
    };
    let capture = match client {
        "cursor" => "interactive_only",
        "claude-desktop" => "unavailable",
        _ => "hook_payloads",
    };
    serde_json::json!({"memory":"local_stdio_bridge","context":context,
        "capture":capture,"constrained_http_mcp":false})
}

pub fn mounts(level: &str, context: &Context) -> Result<Vec<Mount>, Refusal> {
    if let Some(scope) = context.env.get("NEOSIAN_SCOPE").filter(|v| !v.is_empty()) {
        Ok(vec![Mount::new(scope, "memories", (false, false), "")?])
    } else if level == "project" {
        home::project_mounts(&context.cwd)
    } else {
        home::user_mount()?;
        Ok(vec![])
    }
}

pub fn mount_argv(mounts: &[Mount]) -> Vec<String> {
    mounts
        .iter()
        .flat_map(|mount| {
            let policy = if mount.read_only {
                ",ro"
            } else if mount.edit_only {
                ",eo"
            } else {
                ""
            };
            [
                "--mount".into(),
                format!("scope={},path={}{policy}", mount.scope, mount.path),
            ]
        })
        .collect()
}
