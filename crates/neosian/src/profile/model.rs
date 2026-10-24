//! Profile format 1: portable selections only; endpoints belong to local bindings.

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Profile {
    pub profile_format: u32,
    pub workspace_id: String,
    pub name: String,
    pub sources: Vec<String>,
    pub mounts: Vec<Mount>,
    #[serde(default)]
    pub skills: Vec<String>,
    #[serde(default, skip_serializing_if = "std::collections::BTreeMap::is_empty")]
    pub skill_pins: std::collections::BTreeMap<String, crate::skill::model::Pin>,
    #[serde(default)]
    pub context: Context,
    #[serde(default)]
    pub capture: Capture,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub history: Option<History>,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Access {
    Rw,
    #[default]
    Ro,
    Eo,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Injection {
    #[default]
    Index,
    Full,
    None,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Mount {
    pub source: String,
    pub scope: String,
    pub path: String,
    #[serde(default)]
    pub access: Access,
    #[serde(default)]
    pub injection: Injection,
    #[serde(default)]
    pub documents: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Context {
    #[serde(default = "context_budget")]
    pub max_chars: usize,
    #[serde(default)]
    pub mailbox: bool,
}

fn context_budget() -> usize {
    8192
}

impl Default for Context {
    fn default() -> Self {
        Self {
            max_chars: context_budget(),
            mailbox: false,
        }
    }
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum CaptureMode {
    #[default]
    Off,
    Local,
    Shared,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Field {
    Prompt,
    Assistant,
    ToolInputs,
    ToolOutputs,
    ReceivedContext,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Replacement {
    pub from: String,
    pub to: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Capture {
    #[serde(default)]
    pub mode: CaptureMode,
    #[serde(default = "text_fields")]
    pub fields: Vec<Field>,
    #[serde(default)]
    pub exclude_tools: Vec<String>,
    #[serde(default)]
    pub remove_keys: Vec<String>,
    #[serde(default)]
    pub replacements: Vec<Replacement>,
}

fn text_fields() -> Vec<Field> {
    vec![Field::Prompt, Field::Assistant]
}

impl Default for Capture {
    fn default() -> Self {
        Self {
            mode: CaptureMode::Off,
            fields: text_fields(),
            exclude_tools: vec![],
            remove_keys: vec![],
            replacements: vec![],
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct History {
    pub source: String,
    pub scope: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub prefix: Option<String>,
    #[serde(default)]
    pub conversations: Vec<String>,
}

impl Profile {
    pub fn new(name: String) -> Self {
        let workspace_id = uuid::Uuid::new_v4().to_string();
        Self {
            profile_format: 1,
            mounts: vec![Mount {
                source: "workspace".into(),
                scope: format!("workspace:{workspace_id}"),
                path: "project".into(),
                access: Access::Rw,
                injection: Injection::Index,
                documents: vec![],
            }],
            workspace_id,
            name,
            sources: vec!["workspace".into()],
            skills: vec!["builtin:upkeep".into()],
            skill_pins: Default::default(),
            context: Context::default(),
            capture: Capture::default(),
            history: None,
        }
    }

    pub fn conversation_prefix(&self) -> String {
        format!("w-{}-", self.workspace_id.replace('-', ""))
    }
}
