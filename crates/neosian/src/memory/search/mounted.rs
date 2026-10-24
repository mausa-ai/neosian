//! A mounted search preserves backend cursors and adds authorized source references.

use base64::{Engine, engine::general_purpose::URL_SAFE};
use serde_json::{Value, json};

use super::{Page, Request};
use crate::memory::dispatch::Memory;
use crate::memory::mounts::{Mount, resolve};
use crate::store::error::Error;

pub fn selection<'a>(
    mounts: &'a [Mount],
    path: &'a str,
) -> Result<Vec<(&'a Mount, String)>, Error> {
    if !path.starts_with('/') || path.len() > 1154 {
        return Err(Error::Value(
            "search path must be an absolute mounted prefix of at most 1154 bytes".into(),
        ));
    }
    let mut selected = if path == "/" {
        mounts
            .iter()
            .map(|m| (m, String::new()))
            .collect::<Vec<_>>()
    } else {
        let (mount, _) = resolve(mounts, path)?;
        if path[1..].split('/').next() != Some(mount.path.as_str()) {
            return Err(Error::Value(
                "search path must start with exactly one slash".into(),
            ));
        }
        // Preserve a trailing slash: a directory prefix must not match its sibling name.
        let prefix = path[1..].split_once('/').map_or("", |(_, p)| p);
        if prefix.len() > 1024 {
            return Err(Error::Value("search prefix exceeds 1024 bytes".into()));
        }
        vec![(mount, prefix.to_string())]
    };
    selected.sort_by(|a, b| a.0.path.cmp(&b.0.path));
    Ok(selected)
}

pub fn run(memory: &Memory, path: &str, request: &Request) -> Result<Page, Error> {
    let selected = selection(&memory.mounts, path)?;
    let profile = memory.store.profile();
    let mut target = json!({
        "path": path,
        "workspace": profile.map(|p| &p.workspace_id),
        "mounts": selected.iter().map(|(m, prefix)| json!([
            m.path, m.scope, prefix,
            profile.and_then(|p| p.mounts.iter().find(|s| s.scope == m.scope)).map(|m| &m.source)
        ])).collect::<Vec<Value>>()
    });
    let policies: Vec<_> = selected
        .iter()
        .map(|(m, _)| {
            request
                .knowledge_policy
                .selected(memory.store.as_ref(), &m.scope)
        })
        .collect();
    if policies.iter().any(|p| !p.is_all()) {
        target["knowledge_policies"] = json!(policies);
    }
    let position = request.after(&target, "mounted-memory-search-v1")?;
    let (start, backend): (String, Option<String>) = if position.is_empty() {
        (String::new(), None)
    } else {
        serde_json::from_str(&position)
            .map_err(|_| Error::Value("invalid mounted search cursor".into()))?
    };
    let backend = backend
        .map(|token| {
            URL_SAFE
                .decode(token)
                .ok()
                .and_then(|bytes| String::from_utf8(bytes).ok())
                .ok_or_else(|| Error::Value("invalid wrapped backend search cursor".into()))
        })
        .transpose()?;
    if !start.is_empty() && !selected.iter().any(|(m, _)| m.path == start) {
        return Err(Error::Value(
            "search cursor names an unselected mount".into(),
        ));
    }
    let next = |mount: &str, token: Option<String>| {
        // Base64 bounds expansion even for opaque Unicode or control-character tokens.
        let token = token.map(|s| URL_SAFE.encode(s));
        request.cursor_for(
            &target,
            "mounted-memory-search-v1",
            &json!([mount, token]).to_string(),
        )
    };
    let mut result = Page {
        hits: vec![],
        next_cursor: None,
    };
    for (mount, prefix) in selected {
        if mount.path < start {
            continue;
        }
        let mut query = request.clone();
        query.prefix = prefix;
        query.knowledge_policy = request
            .knowledge_policy
            .selected(memory.store.as_ref(), &mount.scope);
        query.cursor = if mount.path == start {
            backend.clone()
        } else {
            None
        };
        query.limit = request.limit.saturating_sub(result.hits.len()).max(1);
        let mut page = memory.store.search_memory(&mount.scope, &query)?;
        query.check_page(&mount.scope, &page)?;
        if result.hits.len() == request.limit {
            if !page.hits.is_empty() {
                result.next_cursor = Some(next(&mount.path, None));
                break;
            }
            continue;
        }
        for hit in &mut page.hits {
            hit.reference = Some(format!("/{}/{}", mount.path, hit.path));
            if let Some(p) = profile {
                hit.workspace_id = Some(p.workspace_id.clone());
                hit.source = p
                    .mounts
                    .iter()
                    .find(|m| m.scope == mount.scope)
                    .map(|m| m.source.clone());
            }
        }
        result.hits.extend(page.hits);
        if let Some(token) = page.next_cursor {
            result.next_cursor = Some(next(&mount.path, Some(token)));
            break;
        }
    }
    Ok(result)
}
