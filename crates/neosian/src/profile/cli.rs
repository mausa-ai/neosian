//! Profile management never opens data stores or activates imported configuration.

use clap::{Args, Subcommand};
use serde_json::{Value, json};
use std::io::{self, Write};
use std::path::PathBuf;

use super::files::{Binding, Endpoint, Project};
use super::model::CaptureMode;
use super::{Profile, files, resolve, validate};

#[derive(Args)]
pub struct Edit {
    /// Apply the preview to local neosian configuration.
    #[arg(long)]
    write: bool,
    /// Explicitly replace an existing profile or binding.
    #[arg(long)]
    replace: bool,
}

#[derive(Subcommand)]
pub enum Command {
    /// Preview a new workspace with capture disabled.
    Init {
        name: String,
        #[command(flatten)]
        edit: Edit,
    },
    /// Validate and preview a portable profile; --write installs it without activation.
    Import {
        name: String,
        file: PathBuf,
        #[command(flatten)]
        edit: Edit,
    },
    /// Print only the portable profile TOML, excluding local bindings and credentials.
    Export { name: String },
    /// Inspect a profile and its local source bindings, without connecting.
    Show {
        name: Option<String>,
        #[arg(long)]
        project: Option<PathBuf>,
    },
    /// Preview or apply source and checkout bindings in the local neosian home.
    Bind(Bind),
}

#[derive(Args)]
pub struct Bind {
    name: String,
    #[arg(long)]
    source: Option<String>,
    #[arg(long)]
    root: Option<PathBuf>,
    #[arg(long)]
    url: Option<String>,
    #[arg(long)]
    token_env: Option<String>,
    #[arg(long)]
    dsn_env: Option<String>,
    #[arg(long)]
    schema: Option<String>,
    #[arg(long, value_parser = ["off", "local", "shared"], default_value = "off")]
    capture: String,
    #[arg(long)]
    project: Option<PathBuf>,
    #[command(flatten)]
    edit: Edit,
}

fn endpoint(b: &Bind) -> Result<Option<Binding>, String> {
    let count = usize::from(b.root.is_some())
        + usize::from(b.url.is_some())
        + usize::from(b.dsn_env.is_some());
    if b.source.is_none() {
        if count != 0 || b.token_env.is_some() || b.schema.is_some() || b.capture != "off" {
            return Err("endpoint and capture options require --source".into());
        }
        return Ok(None);
    }
    if count != 1 {
        return Err("bind --source requires exactly one of --root, --url, --dsn-env".into());
    }
    if b.token_env.is_some() && b.url.is_none() || b.schema.is_some() && b.dsn_env.is_none() {
        return Err("--token-env requires HTTP; --schema requires Postgres".into());
    }
    let endpoint = if let Some(root) = &b.root {
        Endpoint::File {
            root: resolve::absolute(root)?,
        }
    } else if let Some(url) = &b.url {
        Endpoint::Http {
            url: url.clone(),
            token_env: b
                .token_env
                .clone()
                .ok_or("HTTP binding requires --token-env")?,
        }
    } else {
        Endpoint::Postgres {
            dsn_env: b.dsn_env.clone().expect("one endpoint"),
            schema: b.schema.clone().unwrap_or_else(|| "neosian".into()),
        }
    };
    let binding = Binding {
        endpoint,
        capture: match b.capture.as_str() {
            "local" => CaptureMode::Local,
            "shared" => CaptureMode::Shared,
            _ => CaptureMode::Off,
        },
    };
    binding.validate()?;
    Ok(Some(binding))
}

fn bind(b: &Bind) -> Result<Value, String> {
    let home = files::home()?;
    validate::name(&b.name)?;
    let binding = endpoint(b)?;
    if binding.is_none() && b.project.is_none() {
        return Err("bind needs --source or --project".into());
    }
    // Validate everything before creating the home or lock.
    let profile = files::read(&home, &b.name)?;
    if b.source
        .as_ref()
        .is_some_and(|s| !profile.sources.contains(s))
    {
        return Err("cannot bind an undeclared source".into());
    }
    let project = b.project.as_deref().map(resolve::absolute).transpose()?;
    let _lock = b.edit.write.then(|| files::lock(&home)).transpose()?;
    let mut registry = files::registry(&home)?;
    if let Some(binding) = &binding {
        let source = b.source.as_ref().expect("binding has source");
        let sources = registry.profiles.entry(b.name.clone()).or_default();
        if let Some(old) = sources.get(source)
            && json!(old) != json!(binding)
            && !b.edit.replace
        {
            return Err("source binding already exists; use --replace".into());
        }
        sources.insert(source.clone(), binding.clone());
    }
    if let Some(project) = &project {
        if registry
            .projects
            .iter()
            .any(|p| &p.root == project && p.profile != b.name)
            && !b.edit.replace
        {
            return Err("project is already bound; use --replace".into());
        }
        registry.projects.retain(|p| &p.root != project);
        registry.projects.push(Project {
            root: project.clone(),
            profile: b.name.clone(),
        });
        registry.projects.sort_by(|a, b| a.root.cmp(&b.root));
    }
    if b.edit.write {
        files::write_registry(&home, &registry)?;
    }
    Ok(json!({"profile":b.name,"source":b.source,"binding":binding,
        "project":project,"written":b.edit.write}))
}

fn install(name: &str, profile: Profile, edit: &Edit) -> Result<Value, String> {
    validate::name(name)?;
    let home = files::home()?;
    let text = super::encode(&profile)?;
    let path = files::path(&home, name)?;
    if path.exists()
        && !edit.replace
        && std::fs::read_to_string(path).ok().as_deref() != Some(&text)
    {
        return Err("profile already exists; use --replace".into());
    }
    if edit.write {
        let _lock = files::lock(&home)?;
        files::write(&home, name, &profile, edit.replace)?;
    }
    Ok(
        json!({"profile":name,"definition":profile,"written":edit.write,
        "activated":false,"toml":text}),
    )
}

pub fn run(command: &Command, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let answer = (|| match command {
        Command::Init { name, edit } => install(name, Profile::new(name.clone()), edit),
        Command::Import { name, file, edit } => {
            let text = std::fs::read_to_string(file).map_err(|_| "cannot read profile import")?;
            install(name, super::parse(&text)?, edit)
        }
        Command::Export { name } => {
            let p = files::read(&files::home()?, name)?;
            Ok(Value::String(super::encode(&p)?))
        }
        Command::Show { name, project } => {
            let cwd = std::env::current_dir().map_err(|_| "cannot read working directory")?;
            let resolved = resolve::selected(name.as_deref(), project.as_deref().unwrap_or(&cwd))?
                .ok_or("no selected profile")?;
            Ok(resolved.report())
        }
        Command::Bind(b) => bind(b),
    })();
    match answer {
        Ok(Value::String(text)) => write!(out, "{text}").map(|()| 0),
        Ok(value) => writeln!(out, "{}", serde_json::to_string_pretty(&value)?).map(|()| 0),
        Err(message) => crate::cli::usage(true, &message, out, err),
    }
}
