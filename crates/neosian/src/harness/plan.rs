//! What the server hands a turn's watcher (§40.50 item 3, amended by
//! #430): a plan, never a command. The watcher checks every field again
//! and builds the argument vector itself through the same adapter, so
//! `neosian harness-watch` can start only one of the three harnesses with
//! its presets, and only where the tool is enabled and not in a child.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::adapter::{self, Agent, CodexKey, Mode};
use super::codex::KEYS;
use super::text::say;

/// The longest plan line read.
pub const PLAN_BYTES: usize = 64 * 1024;
/// The store flags a plan may carry for the child's neosian server.
const STORE_FLAGS: [&str; 6] = [
    "--root",
    "--url",
    "--schema",
    "--profile",
    "--scope",
    "--mount",
];

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WireKey {
    pub key: String,
    pub disabled: Vec<String>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WirePlan {
    pub agent: String,
    pub mode: String,
    pub directory: String,
    pub prompt_bytes: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resume: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub new_session: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parent: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub codex_keys: Vec<WireKey>,
    /// The server's own store flags, as flag and value pairs.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub store: Vec<String>,
}

/// A plan whose every field was checked.
#[derive(Debug)]
pub struct Checked {
    pub agent: Agent,
    pub mode: Mode,
    pub directory: PathBuf,
    pub model: Option<String>,
    pub resume: Option<String>,
    pub new_session: String,
    pub parent: Option<String>,
    pub codex_keys: Vec<CodexKey>,
    pub store: Vec<String>,
}

fn tool_name(name: &str) -> bool {
    (1..=128).contains(&name.len())
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "_-".contains(c))
}

fn store_ok(store: &[String]) -> bool {
    store.len().is_multiple_of(2)
        && store.chunks(2).all(|pair| {
            STORE_FLAGS.contains(&pair[0].as_str())
                && !pair[1].is_empty()
                && !pair[1].starts_with('-')
                && !pair[1].contains('\0')
        })
}

/// Check a plan as the watcher reads it. `home` and `listed` decide the
/// directory: a listed one, or any but the home directory and the root.
pub fn check(plan: WirePlan, home: Option<&Path>, listed: &[PathBuf]) -> Result<Checked, String> {
    let bad = |field: &str| say("watch_bad_field", &[("field", field)]);
    let agent = Agent::parse(&plan.agent).ok_or_else(|| bad("agent"))?;
    let mode = Mode::parse(&plan.mode).ok_or_else(|| bad("mode"))?;
    if !(1..=adapter::PROMPT_BYTES).contains(&plan.prompt_bytes) {
        return Err(bad("prompt_bytes"));
    }
    if plan.model.as_deref().is_some_and(|m| !adapter::model_ok(m)) {
        return Err(bad("model"));
    }
    if plan
        .resume
        .as_deref()
        .is_some_and(|s| !adapter::session_ok(s))
    {
        return Err(bad("resume"));
    }
    let new_session = match plan.new_session {
        Some(session) => uuid::Uuid::parse_str(&session)
            .map_err(|_| bad("new_session"))?
            .to_string(),
        None => uuid::Uuid::new_v4().to_string(),
    };
    let parent_ok = |p: &str| crate::conversation::ids::parse_conversation_id(p).is_ok();
    if plan.parent.as_deref().is_some_and(|p| !parent_ok(p)) {
        return Err(bad("parent"));
    }
    let mut codex_keys = Vec::new();
    for key in plan.codex_keys {
        let known = KEYS.into_iter().find(|known| *known == key.key);
        let Some(known) = known.filter(|_| key.disabled.iter().all(|t| tool_name(t))) else {
            return Err(bad("codex_keys"));
        };
        codex_keys.push(CodexKey {
            key: known,
            disabled: key.disabled,
        });
    }
    if !store_ok(&plan.store) {
        return Err(bad("store"));
    }
    let directory = Path::new(&plan.directory);
    if !directory.is_absolute() {
        return Err(bad("directory"));
    }
    // From the watcher, the directory is "the server's own": refused when
    // it is the home directory or the root, unless the person listed it.
    let directory = directory
        .canonicalize()
        .ok()
        .filter(|path| path.is_dir())
        .ok_or_else(|| bad("directory"))?;
    let home = home.and_then(|home| home.canonicalize().ok());
    let anywhere = directory.parent().is_some() && Some(&directory) != home.as_ref();
    if !anywhere && !listed.contains(&directory) {
        return Err(bad("directory"));
    }
    Ok(Checked {
        agent,
        mode,
        directory,
        model: plan.model,
        resume: plan.resume,
        new_session,
        parent: plan.parent,
        codex_keys,
        store: plan.store,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plan(directory: &Path) -> WirePlan {
        WirePlan {
            agent: "codex".into(),
            mode: "read-only".into(),
            directory: directory.display().to_string(),
            prompt_bytes: 2,
            ..WirePlan::default()
        }
    }

    #[test]
    fn a_plan_is_checked_field_by_field() {
        let scratch = crate::testing::Scratch::new("harness-plan");
        let work = scratch.path().join("work");
        std::fs::create_dir(&work).unwrap();
        let home = scratch.path();
        assert!(check(plan(&work), Some(home), &[]).is_ok());
        let refused = |edit: &dyn Fn(&mut WirePlan)| {
            let mut wire = plan(&work);
            edit(&mut wire);
            check(wire, Some(home), &[]).is_err()
        };
        assert!(refused(&|p| p.agent = "bash".into()));
        assert!(refused(&|p| p.mode = "admin".into()));
        assert!(refused(&|p| p.prompt_bytes = 0));
        assert!(refused(&|p| p.model = Some("--bare".into())));
        assert!(refused(&|p| p.resume = Some("--last".into())));
        assert!(refused(&|p| p.new_session = Some("not-a-uuid".into())));
        assert!(refused(&|p| p.parent = Some("a b".into())));
        assert!(refused(&|p| p.store = vec!["--exec".into(), "x".into()]));
        assert!(refused(&|p| p.store = vec!["--root".into()]));
        assert!(refused(&|p| p.codex_keys = vec![WireKey {
            key: "other".into(),
            disabled: vec![]
        }]));
        assert!(refused(&|p| p.directory = home.display().to_string()));
        assert!(refused(&|p| p.directory = "/".into()));
        assert!(refused(&|p| p.directory = "relative".into()));
        let listed = [home.canonicalize().unwrap()];
        let mut at_home = plan(home);
        at_home.store = vec!["--root".into(), "/x".into()];
        assert!(check(at_home, Some(home), &listed).is_ok());
        let unknown: Result<WirePlan, _> = serde_json::from_str(
            r#"{"agent":"codex","mode":"write","directory":"/x","prompt_bytes":1,"command":["sh"]}"#,
        );
        assert!(unknown.is_err());
    }
}
