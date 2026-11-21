//! The child as this server's store holds it (§40.50 item 6): the
//! conversation its hooks record it under, which turns landed while a turn
//! ran, and the `- parent:` line its sessions document gains when that
//! document is in a scope this server mounts read-write.

use crate::conversation::sessions::{parent_line, parents, sessions_path};
use crate::memory::dispatch::Memory;
use crate::store::error::Error as StoreError;

/// The conversation the record verb files a native session under for this
/// server: the native ID, or under a profile the ID the profile derives.
pub fn conversation(memory: &Memory, agent: &str, native: &str) -> String {
    let Some(profile) = memory.store.profile() else {
        return native.to_string();
    };
    let installation = crate::profile::files::home()
        .and_then(|home| crate::profile::files::registry(&home))
        .map(|registry| registry.installation_id);
    match installation {
        Ok(installation) => crate::record::profile::conversation_id(
            &profile.conversation_prefix(),
            &installation,
            agent,
            native,
        ),
        Err(_) => native.to_string(),
    }
}

/// The newest turn of a conversation in this store (0 for none).
pub fn last_turn(memory: &Memory, conversation: &str) -> Result<u64, String> {
    memory
        .store
        .last_turn_number(conversation)
        .map_err(|error| error.text())
}

/// How linking went.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Linked {
    Yes,
    NoMount,
    Retry,
    Failed(String),
}

/// Append `- parent: <parent>` to the child's sessions document in the
/// first read-write mount that holds it, against the version read.
pub fn link(memory: &Memory, conversation: &str, parent: &str) -> Linked {
    let path = sessions_path(conversation);
    for mount in memory
        .mounts
        .iter()
        .filter(|mount| !mount.read_only && !mount.edit_only)
    {
        let document = match memory.store.read(&mount.scope, &path) {
            Ok(Some(document)) if !document.envelope.redacted => document,
            Ok(_) => continue,
            Err(error) => return Linked::Failed(crate::conversation::Error::from(error).text()),
        };
        if parents(&document.content)
            .iter()
            .any(|known| known == parent)
        {
            return Linked::Yes;
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
            Ok(_) => Linked::Yes,
            Err(StoreError::Memory(error)) if error.code == "memory_conflict" => Linked::Retry,
            Err(error) => Linked::Failed(crate::conversation::Error::from(error).text()),
        };
    }
    Linked::NoMount
}
