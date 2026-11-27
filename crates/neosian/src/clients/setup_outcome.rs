//! What a setup run ended with, before it is shown: standalone `setup`
//! renders it in full, login in one line for each agent.
use serde_json::Value;

/// How a setup run ended.
pub enum Ran {
    /// Already answered: a usage refusal, `--check`, `--refresh` or `--undo-rename`.
    Exit(u8),
    /// No client is installed; the directories searched.
    NoClient(Vec<Value>),
    /// The checklist was cancelled; nothing was written.
    Cancelled,
    /// The checklist confirmed no client; nothing was written.
    NoneSelected,
    Done(Outcome),
}

/// The selected clients' results, as `setup --json` reports them.
pub struct Outcome {
    pub write: bool,
    pub level: String,
    pub rows: Vec<Value>,
    pub searched: Vec<Value>,
    pub installation: Value,
    pub storage: Value,
    pub failed: bool,
}

/// The parts of one client's result, in the order they are judged.
pub const PARTS: [&str; 4] = ["mcp", "hooks", "cache", "skills"];

#[derive(Debug, PartialEq, Eq)]
pub enum State {
    Connected,
    /// The MCP registration holds; another part did not apply.
    Partial,
    Failed,
}

/// Why a part did not apply.
#[derive(Debug, PartialEq, Eq)]
pub enum Why {
    Refused,
    /// The client's own CLI is not on PATH.
    Missing(String),
    /// The client's own CLI failed.
    Failed(String),
    NotApplied,
}

#[derive(Debug, PartialEq, Eq)]
pub struct Agent {
    pub client: String,
    pub label: String,
    pub state: State,
    /// The first part that did not apply, and why.
    pub problem: Option<(&'static str, Why)>,
}

fn applied(part: &Value, write: bool) -> bool {
    part["success"] == true && (!write || part["applied"] == true)
}

/// One outcome for each selected client.
pub fn agents(outcome: &Outcome) -> Vec<Agent> {
    outcome
        .rows
        .iter()
        .map(|row| {
            let problem = PARTS
                .into_iter()
                .find(|k| !row[k].is_null() && !applied(&row[k], outcome.write))
                .map(|k| (k, why(&row[k])));
            let state = match &problem {
                None => State::Connected,
                Some(("mcp", _)) => State::Failed,
                Some(_) => State::Partial,
            };
            Agent {
                client: text(row, "client"),
                label: text(row, "label"),
                state,
                problem,
            }
        })
        .collect()
}

fn text(row: &Value, key: &str) -> String {
    row[key].as_str().unwrap_or("-").to_owned()
}

fn why(part: &Value) -> Why {
    if part["success"] != true {
        return Why::Refused;
    }
    let Some(apply) = part["apply"].as_str() else {
        return Why::NotApplied;
    };
    let program = shlex::split(apply)
        .and_then(|argv| argv.into_iter().next())
        .map(|first| {
            std::path::Path::new(&first)
                .file_name()
                .map_or(first.clone(), |name| name.to_string_lossy().into_owned())
        })
        .unwrap_or_default();
    if part["apply_error"].is_string() {
        Why::Failed(program)
    } else {
        Why::Missing(program)
    }
}

/// Whether any client took a registration or hooks, so restarting matters.
pub fn restart(outcome: &Outcome) -> bool {
    outcome
        .rows
        .iter()
        .any(|row| row["mcp"]["applied"] == true || row["hooks"]["applied"] == true)
}

/// Whether Codex took user-level hooks it asks a person to review in `/hooks`.
pub fn codex_review(outcome: &Outcome) -> bool {
    outcome.level == "user"
        && outcome.rows.iter().any(|row| {
            row["client"] == "codex"
                && row["hooks"]["applied"] == true
                && row["hooks"]["trust_hint"].is_string()
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn outcome(rows: Vec<Value>) -> Outcome {
        Outcome {
            write: true,
            level: "user".into(),
            rows,
            searched: vec![],
            installation: Value::Null,
            storage: Value::Null,
            failed: false,
        }
    }

    #[test]
    fn each_client_has_one_outcome_and_its_first_problem() {
        let ok = json!({"success": true, "applied": true});
        let done = outcome(vec![
            json!({"client": "cursor", "label": "Cursor", "mcp": ok, "hooks": ok}),
            json!({"client": "codex", "label": "Codex",
                "mcp": {"success": true, "applied": false, "apply": "/usr/local/bin/codex mcp add neosian", "apply_error": null},
                "hooks": {"success": true, "applied": true, "trust_hint": "open /hooks"},
                "skills": {"success": true, "applied": false}}),
            json!({"client": "claude-code", "label": "Claude Code", "mcp": ok, "hooks": ok,
                "cache": {"success": false, "error": "cannot write /x"}}),
            json!({"client": "pi", "label": "Pi", "mcp": {"success": true, "applied": false,
                "apply": "pi add", "apply_error": "exit 1"}, "hooks": ok}),
        ]);
        let agents = agents(&done);
        assert_eq!(agents[0].state, State::Connected);
        assert_eq!(agents[0].problem, None);
        assert_eq!(agents[1].state, State::Failed);
        assert_eq!(
            agents[1].problem,
            Some(("mcp", Why::Missing("codex".into())))
        );
        assert_eq!(agents[2].state, State::Partial);
        assert_eq!(agents[2].problem, Some(("cache", Why::Refused)));
        assert_eq!(agents[3].problem, Some(("mcp", Why::Failed("pi".into()))));
        assert!(restart(&done) && codex_review(&done));
        assert!(!codex_review(&outcome(vec![json!({"client": "codex",
            "hooks": {"success": true, "applied": false, "trust_hint": "x"}})])));
    }
}
