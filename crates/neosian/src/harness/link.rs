//! The child as this server's store holds it (§40.50 item 6, amended by
//! #430): the conversation its hooks record it under, which turns landed
//! while a turn ran, whether an announced session already existed, and the
//! `- parent:` line its sessions document carries. The child's own record
//! hook writes that line where the environment reaches it; the server's
//! write is the fallback, and "linked" is said only once the line is read.

use std::sync::Arc;

use crate::conversation::sessions::{parent_line, parents, sessions_path};
use crate::memory::dispatch::Memory;
use crate::state::State;
use crate::store::error::Error as StoreError;

/// How the record verb names a native session for this server: the native
/// ID, or under a profile the prefix and installation the profile derives
/// it with.
#[derive(Clone)]
pub struct Naming(Option<(String, String)>);

impl Naming {
    pub fn of(memory: &Memory) -> Self {
        let derived = memory.store.profile().and_then(|profile| {
            let installation = crate::profile::files::home()
                .and_then(|home| crate::profile::files::registry(&home))
                .ok()?
                .installation_id;
            Some((profile.conversation_prefix(), installation))
        });
        Self(derived)
    }

    pub fn conversation(&self, agent: &str, native: &str) -> String {
        match &self.0 {
            Some((prefix, installation)) => {
                crate::record::profile::conversation_id(prefix, installation, agent, native)
            }
            None => native.to_string(),
        }
    }
}

/// The newest turn of a conversation in this store (0 for none).
pub fn last_turn(store: &dyn State, conversation: &str) -> Result<u64, String> {
    store
        .last_turn_number(conversation)
        .map_err(|error| error.text())
}

/// Whether a conversation already has turns or a sessions document in one
/// of `scopes`: the check made when a new session is announced.
pub fn existed(store: &Arc<dyn State>, scopes: &[String], conversation: &str) -> bool {
    if last_turn(store.as_ref(), conversation).map_or(true, |turn| turn > 0) {
        return true;
    }
    let path = sessions_path(conversation);
    scopes
        .iter()
        .any(|scope| !matches!(store.read(scope, &path), Ok(None)))
}

/// Where linking stands.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Linked {
    /// The line was read back.
    Yes,
    /// The server wrote the line now; it is confirmed at a later call.
    Written,
    /// No sessions document for the child exists yet.
    Absent,
    /// The document is only in a scope this server cannot write.
    NoMount,
    /// The document there belongs to another agent.
    OtherAgent,
    Retry,
    Failed(String),
}

fn agent_of(content: &str) -> Option<&str> {
    content
        .lines()
        .find_map(|line| line.strip_prefix("- agent: "))
        .map(str::trim)
}

/// Read the child's sessions document; when it lacks the parent line and
/// a read-write mount holds it, write the line against the version read.
pub fn link(memory: &Memory, agent: &str, conversation: &str, parent: &str) -> Linked {
    if crate::conversation::ids::parse_conversation_id(parent).is_err() {
        return Linked::Failed(parent.to_string());
    }
    let path = sessions_path(conversation);
    for mount in &memory.mounts {
        let document = match memory.store.read(&mount.scope, &path) {
            Ok(Some(document)) if !document.envelope.redacted => document,
            Ok(_) => continue,
            Err(error) => return Linked::Failed(crate::conversation::Error::from(error).text()),
        };
        if agent_of(&document.content) != Some(agent) {
            return Linked::OtherAgent;
        }
        if parents(&document.content)
            .iter()
            .any(|known| known == parent)
        {
            return Linked::Yes;
        }
        if mount.read_only || mount.edit_only {
            return Linked::NoMount;
        }
        let mut content = document.content.clone();
        if !content.is_empty() && !content.ends_with('\n') {
            content.push('\n');
        }
        content.push_str(&parent_line(parent));
        return match memory.store.write(
            &mount.scope,
            &path,
            &content,
            &memory.actor,
            Some(document.envelope.version),
        ) {
            Ok(_) => Linked::Written,
            Err(StoreError::Memory(error)) if error.code == "memory_conflict" => Linked::Retry,
            Err(error) => Linked::Failed(crate::conversation::Error::from(error).text()),
        };
    }
    Linked::Absent
}
