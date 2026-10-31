//! Install a small host-owned mod through Claude's public plugin CLI.

use super::{
    cache_shell,
    context::Context,
    files::{self, Failure},
    runner, targets,
};
use serde_json::{Value, json};
use std::path::{Path, PathBuf};

pub const PLUGIN: &str = "neosian-cache@neosian-local";
const ASSETS: &[(&str, &str)] = &[
    (
        ".claude-plugin/plugin.json",
        include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/clients/claude-cache/.claude-plugin/plugin.json"
        )),
    ),
    (
        "hooks/hooks.json",
        include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/clients/claude-cache/hooks/hooks.json"
        )),
    ),
    (
        "hooks/register.js",
        include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/clients/claude-cache/hooks/register.js"
        )),
    ),
    (
        "hooks/evidence.js",
        include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/clients/claude-cache/hooks/evidence.js"
        )),
    ),
    (
        "hooks/display.js",
        include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/clients/claude-cache/hooks/display.js"
        )),
    ),
    (
        "assets/refresh.md",
        include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/clients/claude-cache/assets/refresh.md"
        )),
    ),
    (
        "package.json",
        include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/clients/claude-cache/package.json"
        )),
    ),
    (
        "README.md",
        include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/clients/claude-cache/README.md"
        )),
    ),
];

fn directory(context: &Context, level: &str) -> PathBuf {
    targets::hooks("claude-code", context, level)
        .path
        .parent()
        .unwrap()
        .join("neosian-cache")
}

fn prior(context: &Context, level: &str, document: &Value) -> Result<Value, Failure> {
    let selected = directory(context, level);
    let mut original = document.get("statusLine").cloned().unwrap_or(Value::Null);
    let mut source = selected;
    if original.is_null() && level == "project" {
        let user = targets::hooks("claude-code", context, "user");
        original = files::load(&user.path)?
            .get("statusLine")
            .cloned()
            .unwrap_or(Value::Null);
        source = directory(context, "user");
    }
    if let Some(saved) = files::read(&source.join("statusline.json"))
        && saved["wrapper"] == original["command"]
        && original["command"].is_string()
    {
        original = saved["original"].clone();
    }
    if !original.is_null() && (original["type"] != "command" || !original["command"].is_string()) {
        return Err(Failure::new(
            "Cannot compose this Claude statusline",
            "Keep its configuration and use setup --claude-cache off",
        ));
    }
    Ok(original)
}

fn wrapper(context: &Context, directory: &Path) -> String {
    cache_shell::wrapper(&[
        context.executable.clone(),
        "statusline".into(),
        "--client".into(),
        "claude-code".into(),
        "--bridge".into(),
        directory.display().to_string(),
    ])
}

pub fn install(context: &Context, level: &str, write: bool) -> Value {
    let target = targets::hooks("claude-code", context, level);
    match apply(context, level, write) {
        Ok(value) => value,
        Err(error) => error.payload(&target),
    }
}

fn apply(context: &Context, level: &str, write: bool) -> Result<Value, Failure> {
    let target = targets::hooks("claude-code", context, level);
    files::evidence(&target)?;
    let document = files::load(&target.path)?;
    if !document["enabledPlugins"].is_null() && !document["enabledPlugins"].is_object() {
        return Err(Failure::new("enabledPlugins is not an object", files::FIX));
    }
    if document["enabledPlugins"][PLUGIN] == false {
        return disable_inner(context, level, write);
    }
    let original = prior(context, level, &document)?;
    let root = directory(context, level);
    let selected = document.get("statusLine").cloned().unwrap_or(Value::Null);
    let restore = files::read(&root.join("statusline.json"))
        .filter(|saved| selected["command"].is_string() && saved["wrapper"] == selected["command"])
        .map(|saved| saved.get("restore").unwrap_or(&saved["original"]).clone())
        .unwrap_or(selected);
    let command = wrapper(context, &root);
    let mut statusline = if original.is_null() {
        json!({"type":"command", "padding":0})
    } else {
        original.clone()
    };
    statusline["command"] = json!(command);
    // Keep the fallback warning current even when the mod fails to load.
    // Preserve a faster user interval; restore the original value on disable.
    if !statusline["refreshInterval"]
        .as_u64()
        .is_some_and(|n| (1..=30).contains(&n))
    {
        statusline["refreshInterval"] = json!(30);
    }
    let add = vec![
        "claude".into(),
        "plugin".into(),
        "marketplace".into(),
        "add".into(),
        root.display().to_string(),
        "--scope".into(),
        level.into(),
    ];
    let install = vec![
        "claude".into(),
        "plugin".into(),
        "install".into(),
        PLUGIN.into(),
        "--scope".into(),
        level.into(),
    ];
    let available = runner::which("claude", &context.env).is_some();
    let mut report = json!({"success":true,"client":"claude-code","config_path":target.path,
        "directory":root,"plugin":PLUGIN,"written":false,"applied":false,
        "commands":[add,install],"statusline":statusline,"preserves_statusline":!original.is_null(),
        "disabled":false,"cli_available":available,
        "note":"Claude owns model requests. Keep-warm preserves observed one-hour caches while idle; expired or uncertain caches wait for your next message."});
    if !write {
        return Ok(report);
    }
    for (name, text) in ASSETS {
        files::write_text(&root.join("plugin").join(name), text)?;
    }
    files::write(
        &root.join("plugin/bridge.json"),
        &json!({"observations":root.join("observations")}),
        false,
    )?;
    files::write(
        &root.join(".claude-plugin/marketplace.json"),
        &json!({
            "name":"neosian-local", "owner":{"name":"Mausa"},
            "plugins":[{"name":"neosian-cache", "source":"./plugin"}]
        }),
        false,
    )?;
    crate::store::fileio::private_mkdir(&root.join("observations"))?;
    for argv in [&add, &install] {
        match runner::run(argv, &context.env) {
            Some((true, _)) => {}
            result => {
                report["success"] = json!(false);
                report["error"] = json!(
                    result
                        .map(|(_, why)| why)
                        .unwrap_or_else(|| "Claude CLI is not on PATH".into())
                );
                report["hint"] = json!(
                    "Install/update Claude Code, then repeat neosian setup --client claude-code --write"
                );
                return Ok(report);
            }
        }
    }
    // The plugin CLI writes settings. Reload so its registrations survive.
    let mut document = files::load(&target.path)?;
    files::write(
        &root.join("statusline.json"),
        &json!({"format":1,"original":original,"restore":restore,"wrapper":command}),
        false,
    )?;
    document["statusLine"] = statusline;
    files::write(&target.path, &document, false)?;
    report["written"] = json!(true);
    report["applied"] = json!(true);
    Ok(report)
}

pub fn status(context: &Context) -> Value {
    let levels: Vec<_> = ["user", "project"]
        .into_iter()
        .map(|level| {
            let target = targets::hooks("claude-code", context, level);
            let settings = files::read(&target.path).unwrap_or(Value::Null);
            let root = directory(context, level);
            let saved = files::read(&root.join("statusline.json")).unwrap_or(Value::Null);
            let installed = root.join("plugin/.claude-plugin/plugin.json").is_file();
            let current = installed
                && ASSETS.iter().all(|(name, bytes)| {
                    std::fs::read_to_string(root.join("plugin").join(name))
                        .is_ok_and(|found| found == *bytes)
                });
            let observed = saved["wrapper"].is_string()
                && settings["statusLine"]["command"] == saved["wrapper"]
                && files::read(&root.join("plugin/bridge.json"))
                    .is_some_and(|v| v["observations"] == json!(root.join("observations")));
            json!({"level":level,"installed":installed,"current":current,
            "enabled":settings["enabledPlugins"][PLUGIN],"observation_configured":observed,
            "directory":root,"runtime":"not_observed"})
        })
        .collect();
    json!({"plugin":PLUGIN,"levels":levels,"minimum_claude_version":"2.1.314",
        "runtime":"not_observed"})
}

pub fn disable(context: &Context, level: &str, write: bool) -> Value {
    disable_inner(context, level, write)
        .unwrap_or_else(|e| e.payload(&targets::hooks("claude-code", context, level)))
}

fn disable_inner(context: &Context, level: &str, write: bool) -> Result<Value, Failure> {
    let target = targets::hooks("claude-code", context, level);
    files::evidence(&target)?;
    let mut document = files::load(&target.path)?;
    if !document["enabledPlugins"].is_null() && !document["enabledPlugins"].is_object() {
        return Err(Failure::new("enabledPlugins is not an object", files::FIX));
    }
    let root = directory(context, level);
    if let Some(saved) = files::read(&root.join("statusline.json"))
        && saved["wrapper"].is_string()
        && document["statusLine"]["command"] == saved["wrapper"]
    {
        let restore = saved.get("restore").unwrap_or(&saved["original"]);
        if restore.is_null() {
            document.as_object_mut().unwrap().remove("statusLine");
        } else {
            document["statusLine"] = restore.clone();
        }
    }
    if document["enabledPlugins"].is_null() {
        document["enabledPlugins"] = json!({});
    }
    document["enabledPlugins"][PLUGIN] = json!(false);
    if write {
        files::write(&target.path, &document, false)?;
    }
    Ok(
        json!({"success":true,"client":"claude-code","config_path":target.path,
        "disabled":true,"written":write,"applied":write,
        "note":"Keep-warm disabled; an owned statusline is restored. Other client registrations are unchanged."}),
    )
}
