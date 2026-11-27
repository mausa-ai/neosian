//! Context assembly under one strict character budget, with explicit source references.
//! The history source's sessions are read as light records while it keeps
//! records (`records`).

use super::model::Injection;
use crate::assets::{CONTEXT, literal, render};
use crate::conversation::Error;
use crate::memory::mounts::Mount;
use crate::record::context::History;
use crate::state::State;
use crate::state::records::Records;
use crate::store::time::Stamp;

fn template(key: &str) -> String {
    literal(CONTEXT, key).expect("profile context asset")
}

pub fn identity(id: &str) -> String {
    let template =
        crate::assets::block(crate::assets::MESSAGES, "identity").expect("identity asset");
    render(&template, &[("session", id)]).trim_end().to_string()
}

// OpenCode receives workspace context from MCP and the current session from
// the plugin. Both contributions share the configured character budget.
pub fn opencode_hook_limit(profile: &super::Profile) -> usize {
    if profile.context.mailbox {
        profile
            .context
            .max_chars
            .min(crate::record::context::context_chars("opencode") as usize)
            / 2
    } else {
        identity("").chars().count() + profile.conversation_prefix().len() + 64
    }
}

struct Budget {
    text: String,
    limit: usize,
    omitted: usize,
}

impl Budget {
    fn push(&mut self, text: &str) {
        if self.text.chars().count() + text.chars().count() + 2 <= self.limit {
            if !self.text.is_empty() {
                self.text.push('\n');
            }
            self.text.push_str(text);
        } else {
            self.omitted += 1;
        }
    }
}

pub fn render_context(
    store: &dyn State,
    records: Option<&dyn Records>,
    mounts: &[Mount],
    limit: usize,
    own: Option<(&str, &str)>,
) -> Result<String, Error> {
    let p = store
        .profile()
        .expect("profile context uses a routed store");
    let now = Stamp::now();
    let limit = limit.min(p.context.max_chars);
    let limit = if own.is_some() {
        limit.saturating_sub(template("profile_instructions").chars().count())
    } else {
        limit
    };
    let footer = template("profile_omitted");
    let mut b = Budget {
        text: String::new(),
        limit: limit.saturating_sub(footer.chars().count() + 32),
        omitted: 0,
    };
    if let Some((id, _)) = own {
        b.push(&identity(id));
    }
    b.push(&render(
        &template("profile_header"),
        &[("workspace", &p.workspace_id), ("name", &p.name)],
    ));
    for m in &p.mounts {
        if m.injection == Injection::None {
            continue;
        }
        b.push(&format!(
            "## /{} [source={}, scope={}, access={:?}]",
            m.path, m.source, m.scope, m.access
        ));
        match m.injection {
            Injection::Index => {
                let entries = store.list_documents(&m.scope, "")?;
                if !m.knowledge_policy.is_all() && entries.len() > 10_000 {
                    return Err(crate::knowledge::model::invalid(
                        "filtered context exceeds 10000 entries",
                    )
                    .into());
                }
                for entry in entries {
                    if crate::memory::mounts::state_path(&entry.path)
                        || entry.path.starts_with("sessions/")
                    {
                        continue;
                    }
                    if !m.knowledge_policy.is_all() {
                        let Some(doc) = store.read(&m.scope, &entry.path)? else {
                            continue;
                        };
                        if doc.envelope.redacted
                            || !crate::knowledge::governance::eligible(
                                m.knowledge_policy,
                                &m.scope,
                                &entry.path,
                                &doc.content,
                                now,
                            )
                        {
                            continue;
                        }
                    }
                    b.push(&format!(
                        "- /{}/{} (v{}{}, source={}, scope={})",
                        m.path,
                        entry.path,
                        entry.version,
                        if entry.redacted { ", redacted" } else { "" },
                        m.source,
                        m.scope
                    ));
                }
            }
            Injection::Full => {
                for path in &m.documents {
                    match store.read(&m.scope, path)? {
                        Some(doc)
                            if !doc.envelope.redacted
                                && crate::knowledge::governance::eligible(
                                    m.knowledge_policy,
                                    &m.scope,
                                    path,
                                    &doc.content,
                                    now,
                                ) =>
                        {
                            b.push(&format!(
                                "/{}/{} (v{}, source={}, scope={}):\n{}",
                                m.path, path, doc.envelope.version, m.source, m.scope, doc.content
                            ))
                        }
                        _ => b.push(&render(
                            &template("profile_missing"),
                            &[("path", &format!("/{}/{path}", m.path))],
                        )),
                    }
                }
            }
            Injection::None => {}
        }
    }
    for skill in
        crate::memory::skill::list_skills(store, mounts, &crate::memory::skill::shipped_skills())?
    {
        b.push(&format!(
            "- {}{}: {}",
            skill.name,
            skill
                .version
                .map(|v| format!(" (v{v})"))
                .unwrap_or_default(),
            skill
                .skill
                .as_ref()
                .map(|s| s.description.as_str())
                .or(skill.error.as_deref())
                .unwrap_or("")
        ));
    }
    if p.context.mailbox {
        b.push(&crate::messaging::delivery::context(
            &crate::messaging::mailbox::Mailbox::of_mounts(store, mounts, own.map(|(id, _)| id)),
        )?);
    }
    if let Some(history) = &p.history {
        let left = b.limit.saturating_sub(b.text.chars().count() + 2);
        if left > 512 {
            let (id, source) = own.unwrap_or(("", "startup"));
            let context = crate::record::context::render_start_context(
                History::new(store, records),
                &history.scope,
                "history",
                (id, source),
                Stamp::now(),
                left as isize,
            )?;
            b.push(&context);
        } else {
            b.omitted += 1;
        }
    }
    if b.omitted > 0 {
        b.text.push('\n');
        b.text
            .push_str(&render(&footer, &[("count", &b.omitted.to_string())]));
    }
    Ok(b.text)
}
