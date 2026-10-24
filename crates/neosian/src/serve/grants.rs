//! Opt-in exact-scope grants, intersected with existing token allowances.

use super::envelope::Fail;
use super::params::Payload;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Scope {
    scope: String,
    #[serde(default)]
    read: bool,
    #[serde(default)]
    write: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Conversations {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    prefix: Option<String>,
    #[serde(default)]
    ids: Vec<String>,
    #[serde(default)]
    read: bool,
    #[serde(default)]
    append: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Grant {
    actor: String,
    #[serde(default)]
    scopes: Vec<Scope>,
    #[serde(default)]
    conversations: Vec<Conversations>,
    #[serde(default)]
    operator: bool,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Grants {
    grant_format: u32,
    clients: Vec<Grant>,
}

impl Grants {
    pub fn load(actors: &[&str]) -> Result<Option<Self>, String> {
        let Some(path) = std::env::var_os("NEOSIAN_SERVE_GRANTS").filter(|v| !v.is_empty()) else {
            return Ok(None);
        };
        let text = std::fs::read_to_string(path).map_err(|_| "cannot read NEOSIAN_SERVE_GRANTS")?;
        if text.len() > 1_048_576 {
            return Err("grants exceed 1 MiB".into());
        }
        let grants: Self =
            toml::from_str(&text).map_err(|_| "invalid grants TOML or unknown/mistyped field")?;
        if grants.grant_format != 1 {
            return Err("unsupported grant_format; expected 1".into());
        }
        let mut seen = std::collections::HashSet::new();
        for client in &grants.clients {
            if !actors.contains(&client.actor.as_str()) || !seen.insert(&client.actor) {
                return Err("grants require unique authenticated client actors".into());
            }
            let mut scopes = std::collections::HashSet::new();
            for scope in &client.scopes {
                crate::store::grammar::check_scope(&scope.scope)
                    .map_err(|_| "invalid grant scope")?;
                if !scopes.insert(&scope.scope) {
                    return Err("duplicate scope grant".into());
                }
            }
            for conversations in &client.conversations {
                if conversations.prefix.is_some() == !conversations.ids.is_empty() {
                    return Err("conversation grants require either prefix or ids".into());
                }
                for id in conversations.prefix.iter().chain(&conversations.ids) {
                    crate::conversation::ids::check_conversation_id(id)
                        .map_err(|_| "invalid grant conversation namespace/ID")?;
                }
            }
        }
        Ok(Some(grants))
    }

    fn get(&self, actor: &str) -> Option<&Grant> {
        self.clients.iter().find(|c| c.actor == actor)
    }

    pub fn capability(&self) -> Value {
        json!({"version":1,"mode":"scoped"})
    }

    pub fn allowed(&self, actor: &str, route: &str, p: &Payload, whole: bool) -> Option<Fail> {
        let deny = || {
            Some(Fail::forbidden(
                "the requested operation under this client's grants",
            ))
        };
        let Some(g) = self.get(actor) else {
            return deny();
        };
        if whole {
            return if g.operator { None } else { deny() };
        }
        let erase = route.ends_with("/redact")
            || route.ends_with("/redact_turns")
            || route == "memory/prune_changes";
        if erase && !g.operator {
            return deny();
        }
        if route.starts_with("memory/") {
            let write = matches!(
                route,
                "memory/write"
                    | "memory/delete"
                    | "memory/rename"
                    | "memory/redact"
                    | "memory/prune_changes"
            );
            return match p.get("scope").and_then(Value::as_str) {
                Some(scope)
                    if g.scopes
                        .iter()
                        .any(|s| s.scope == scope && if write { s.write } else { s.read }) =>
                {
                    None
                }
                _ => deny(),
            };
        }
        let append = matches!(
            route,
            "conversation/append_turn" | "conversation/append_projections"
        ) || erase;
        let allowed = |id: &str| {
            g.conversations.iter().any(|c| {
                (if append { c.append } else { c.read })
                    && (c.prefix.as_ref().is_some_and(|p| id.starts_with(p))
                        || c.ids.iter().any(|c| c == id))
            })
        };
        if matches!(
            route,
            "conversation/search_turns" | "conversation/turn_redactions"
        ) {
            return match p.get("conversations").and_then(Value::as_array) {
                Some(ids) if ids.iter().all(|id| id.as_str().is_some_and(allowed)) => None,
                _ => deny(),
            };
        }
        match p.get("conversation_id").and_then(Value::as_str) {
            Some(id) if allowed(id) => None,
            _ => deny(),
        }
    }
}
