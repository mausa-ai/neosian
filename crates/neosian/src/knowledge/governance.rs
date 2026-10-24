//! Content review assertions and retrieval selection, never access authority.

use serde::{Deserialize, Serialize};
use serde_json::json;

use super::model::{Content, digest, invalid, ordinary};
use crate::state::State;
use crate::store::{error::Error, grammar::parse_scope, time::Stamp};

pub const KEY: &str = "neosian_governance";

#[derive(
    Clone,
    Copy,
    Debug,
    Default,
    Deserialize,
    Serialize,
    PartialEq,
    Eq,
    PartialOrd,
    Ord,
    clap::ValueEnum,
)]
#[serde(rename_all = "lowercase")]
pub enum Policy {
    #[default]
    All,
    Effective,
    Approved,
}

impl Policy {
    pub fn is_all(&self) -> bool {
        *self == Self::All
    }

    pub fn accepts(self, status: &str) -> bool {
        self == Self::All
            || status == "approved"
            || (self == Self::Effective && status == "unmanaged")
    }

    pub fn selected(self, store: &dyn State, scope: &str) -> Self {
        self.max(
            store
                .profile()
                .and_then(|p| p.mounts.iter().find(|m| m.scope == scope))
                .map_or(Self::All, |m| m.knowledge_policy),
        )
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ReviewState {
    Draft,
    Approved,
    Superseded,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    Knowledge,
    Procedure,
    Decision,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Link {
    pub scope: String,
    pub path: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Review {
    pub by: String,
    pub at: String,
    pub sha256: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Record {
    pub format: u32,
    pub kind: Kind,
    pub owners: Vec<String>,
    pub state: ReviewState,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub effective_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub superseded_by: Option<Link>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub review: Option<Review>,
}

pub fn label(text: &str) -> Result<(), Error> {
    if text.trim().is_empty() || text.chars().count() > 256 || text.chars().any(char::is_control) {
        return Err(invalid(
            "governance labels require 1..256 characters without controls",
        ));
    }
    Ok(())
}

pub fn timestamp(text: &str) -> Result<Stamp, Error> {
    let bytes = text.as_bytes();
    let valid = bytes.len() == 20
        || ((22..=27).contains(&bytes.len())
            && bytes[19] == b'.'
            && bytes[20..bytes.len() - 1].iter().all(u8::is_ascii_digit));
    if !valid
        || bytes.get(10) != Some(&b'T')
        || bytes.get(16) != Some(&b':')
        || !text.ends_with('Z')
    {
        return Err(invalid(
            "governance timestamps require UTC YYYY-MM-DDTHH:MM:SS[.ffffff]Z",
        ));
    }
    Stamp::parse(text).map_err(|_| invalid("invalid governance timestamp"))
}

impl Record {
    pub fn validate(&self) -> Result<(), Error> {
        if self.format != 1 || self.owners.is_empty() || self.owners.len() > 16 {
            return Err(invalid("governance requires format 1 and 1..16 owners"));
        }
        let mut owners = std::collections::BTreeSet::new();
        for owner in &self.owners {
            label(owner)?;
            if !owners.insert(owner) {
                return Err(invalid("duplicate governance owner"));
            }
        }
        let effective = self.effective_at.as_deref().map(timestamp).transpose()?;
        let expiry = self.expires_at.as_deref().map(timestamp).transpose()?;
        if effective
            .zip(expiry)
            .is_some_and(|(a, b)| a.instant() >= b.instant())
        {
            return Err(invalid("expiry must be after the effective instant"));
        }
        if let Some(link) = &self.superseded_by {
            parse_scope(&link.scope)?;
            ordinary(&link.path)?;
            if self.state != ReviewState::Superseded {
                return Err(invalid("only superseded knowledge can name a successor"));
            }
        }
        if self.review.is_some() != (self.state == ReviewState::Approved) {
            return Err(invalid("only approved knowledge requires a review record"));
        }
        if let Some(review) = &self.review {
            label(&review.by)?;
            timestamp(&review.at)?;
            if review.sha256.len() != 64
                || !review
                    .sha256
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            {
                return Err(invalid("invalid review SHA-256"));
            }
        }
        Ok(())
    }
}

/// Compact UTF-8 JSON with recursively sorted objects. No envelope or review record.
pub fn fingerprint(scope: &str, path: &str, content: &Content) -> String {
    let mut metadata = serde_json::to_value(&content.metadata).expect("metadata serializes");
    if let Some(record) = metadata["attributes"][KEY].as_object_mut() {
        record.remove("review");
    }
    let mut value = json!([1, scope, path, metadata, content.body]);
    value.sort_all_objects();
    digest(serde_json::to_vec(&value).expect("review content serializes"))
}

pub fn record(content: &Content) -> Result<Option<Record>, Error> {
    content
        .metadata
        .attributes
        .get(KEY)
        .map(|value| {
            let record: Record = serde_json::from_value(value.clone())
                .map_err(|_| invalid("invalid governance record"))?;
            record.validate()?;
            Ok(record)
        })
        .transpose()
}

pub fn status(scope: &str, path: &str, text: &str, now: Stamp) -> &'static str {
    if !text.starts_with("<!-- neosian-knowledge:") {
        return "unmanaged";
    }
    let Ok(content) = Content::parse(text, "knowledge") else {
        return "invalid";
    };
    let record = match record(&content) {
        Ok(Some(record)) => record,
        Ok(None) => return "unmanaged",
        Err(_) => return "invalid",
    };
    match record.state {
        ReviewState::Draft => return "draft",
        ReviewState::Superseded => return "superseded",
        ReviewState::Approved => {}
    }
    let review = record.review.as_ref().expect("validated review");
    if review.sha256 != fingerprint(scope, path, &content) {
        return "stale";
    }
    if timestamp(&review.at).expect("validated time").instant() > now.instant() {
        return "scheduled";
    }
    if record
        .expires_at
        .as_deref()
        .is_some_and(|s| timestamp(s).expect("validated time").instant() <= now.instant())
    {
        return "expired";
    }
    if record
        .effective_at
        .as_deref()
        .is_some_and(|s| timestamp(s).expect("validated time").instant() > now.instant())
    {
        return "scheduled";
    }
    "approved"
}

pub fn eligible(policy: Policy, scope: &str, path: &str, text: &str, now: Stamp) -> bool {
    policy.is_all() || policy.accepts(status(scope, path, text, now))
}
