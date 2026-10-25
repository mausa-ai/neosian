//! Installation identity and configuration findings; never execute registered commands.

use super::{context::Context, runner, settings};
use serde_json::{Value, json};
use std::path::Path;

pub fn executable(command: Option<&str>, context: &Context) -> Value {
    let resolved = command.and_then(|s| runner::which(s, &context.env));
    let resolved = resolved.map(|p| super::context::absolute(&context.cwd, &p));
    let selected = super::context::absolute(&context.cwd, Path::new(&context.executable));
    json!({"command":command,"resolved":resolved,
        "matches_selected":resolved.as_ref().map(|p| p == &selected)})
}

pub fn installation(context: &Context) -> Value {
    let running = std::env::current_exe()
        .ok()
        .map(|p| super::context::absolute(&context.cwd, &p));
    let paths = crate::shape::paths(&context.env, &context.cwd);
    let selected = executable(Some(&context.executable), context);
    let mut warnings = Vec::new();
    if let Some(first) = paths.first()
        && running
            .as_ref()
            .is_some_and(|p| p != &super::context::absolute(&context.cwd, Path::new(first)))
    {
        warnings.push("PATH selects another installation; use the running executable's absolute path for setup and checks");
    }
    json!({"running":running,"version":crate::version::VERSION,
        "selected":selected,"path_executables":paths,"warnings":warnings})
}

fn flag<'a>(argv: &'a [String], name: &str) -> Option<&'a str> {
    argv.iter().enumerate().find_map(|(i, arg)| {
        if arg == name {
            argv.get(i + 1).map(String::as_str)
        } else {
            arg.strip_prefix(name).and_then(|s| s.strip_prefix('='))
        }
    })
}

pub fn storage(argv: &[String], context: &Context) -> Value {
    let mut report = selection(
        flag(argv, "--root"),
        flag(argv, "--url"),
        flag(argv, "--profile"),
        context,
    );
    if let Some(schema) = flag(argv, "--schema") {
        if report["kind"] != "postgres" || crate::postgres::validate_schema(schema).is_err() {
            return json!({"ready":false,"error":"--schema requires Postgres and a valid schema name","access":"not_checked"});
        }
        report["schema"] = json!(schema);
    }
    report
}

pub fn selection(
    root: Option<&str>,
    url: Option<&str>,
    profile: Option<&str>,
    context: &Context,
) -> Value {
    // Client-owned files may contain unsupported URLs with inline secrets. Do
    // not repeat those values in a diagnostic, even when validation fails.
    if let Some(url) = url {
        let valid = url.parse::<ureq::http::Uri>().is_ok_and(|uri| {
            matches!(uri.scheme_str(), Some("http" | "https"))
                && uri.authority().is_some_and(|a| !a.as_str().contains('@'))
                && uri.query().is_none()
                && !url.contains('#')
        });
        if !valid {
            return json!({"ready":false,"error":"HTTP endpoint must exclude credentials, query and fragment; use a token environment variable","access":"not_checked"});
        }
    }
    let settings = match settings::resolve_profile(root, url, profile, false, context) {
        Ok(settings) => settings,
        Err(error) => return json!({"ready":false,"error":error,"access":"not_checked"}),
    };
    let missing: Vec<_> = settings
        .credentials
        .iter()
        .filter(|name| context.env.get(*name).is_none_or(|s| s.is_empty()))
        .collect();
    if let Some(profile) = settings.profile {
        return json!({"kind":"profile","ready":profile["ready"],"capture":profile["capture"],
            "profile":profile,"access":"not_checked"});
    }
    let kind = match settings.store.first().map(String::as_str) {
        Some("--root") => "files",
        Some("--url") => "http",
        _ => "postgres",
    };
    let mut report = json!({"kind":kind,"location":settings.store.get(1),"capture":"enabled",
        "required_environment":settings.credentials,"missing_environment":missing,
        "ready":missing.is_empty(),"access":"not_checked"});
    if kind == "postgres" {
        report["schema"] = json!("neosian");
    }
    report
}

pub fn registration(argv: Option<&Vec<String>>, context: &Context) -> Value {
    match argv {
        Some(argv) => json!({"executable":executable(argv.first().map(String::as_str), context),
            "storage":storage(argv, context)}),
        None => Value::Null,
    }
}
