//! Context assembly under one strict character budget, with explicit source references.

use super::model::Injection;
use crate::assets::{CONTEXT, literal, render};
use crate::conversation::Error;
use crate::memory::mounts::Mount;
use crate::state::State;
use crate::store::time::Stamp;

fn template(key: &str) -> String {
    literal(CONTEXT, key).expect("profile context asset")
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
        let identity =
            crate::assets::block(crate::assets::MESSAGES, "identity").expect("identity asset");
        b.push(&render(&identity, &[("session", id)]));
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
                store,
                &history.scope,
                "history",
                id,
                source,
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
