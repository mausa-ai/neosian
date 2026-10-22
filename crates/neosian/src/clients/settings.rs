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
    pub credentials: Vec<&'static str>,
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
        settings.credentials.push("NEOSIAN_CLIENT_TOKEN");
    } else if dsn {
        settings.credentials.push("NEOSIAN_POSTGRES_DSN");
    } else {
        let root = root_arg.map_or(root, |s| expand(s, &context.home));
        settings.store = vec![
            "--root".into(),
            absolute(&context.cwd, &root).display().to_string(),
        ];
    }
    Ok(settings)
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
