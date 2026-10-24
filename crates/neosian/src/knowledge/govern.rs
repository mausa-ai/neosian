//! Review proposals reuse upkeep's current authority, provenance and apply guards.

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use super::governance::{self, KEY, Kind, Link, Policy, Record, Review, ReviewState};
use super::model::{Content, invalid};
use crate::memory::dispatch::Memory;
use crate::store::{error::Error, time::Stamp};
use crate::upkeep::model::{Edit, Operation, Proposal, Request};

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Change {
    pub format: u32,
    pub kind: Kind,
    pub owners: Vec<String>,
    pub state: ReviewState,
    #[serde(default)]
    pub effective_at: Option<String>,
    #[serde(default)]
    pub expires_at: Option<String>,
    #[serde(default)]
    pub superseded_by: Option<Link>,
    #[serde(default)]
    pub reviewer: Option<String>,
}

impl Change {
    pub fn parse(raw: &str) -> Result<Self, Error> {
        if raw.len() > super::model::MAX_METADATA {
            return Err(invalid("governance input exceeds 64 KiB"));
        }
        let change: Self = super::json::parse(raw)?;
        change.record(Stamp::now())?;
        Ok(change)
    }

    fn record(&self, now: Stamp) -> Result<Record, Error> {
        let record = Record {
            format: self.format,
            kind: self.kind,
            owners: self.owners.clone(),
            state: self.state,
            effective_at: self.effective_at.clone(),
            expires_at: self.expires_at.clone(),
            superseded_by: self.superseded_by.clone(),
            review: self.reviewer.as_ref().map(|by| Review {
                by: by.clone(),
                at: now.utc().z(),
                sha256: "0".repeat(64),
            }),
        };
        record.validate()?;
        Ok(record)
    }
}

pub fn prepare(
    memory: &Memory,
    path: &str,
    change: &Change,
    expected: u64,
    reason: &str,
) -> Result<Proposal, Error> {
    let (mount, name) = super::target(memory, path)?;
    let mut record = change.record(Stamp::now())?;
    if record
        .superseded_by
        .as_ref()
        .is_some_and(|link| link.scope == mount.scope && link.path == name)
    {
        return Err(invalid("knowledge cannot supersede itself"));
    }
    let mut attributes = serde_json::Map::new();
    attributes.insert(
        KEY.into(),
        serde_json::to_value(&record).expect("record serializes"),
    );
    let mut proposal = crate::upkeep::prepare(
        memory,
        &Request {
            format: 1,
            changes: vec![Edit {
                path: path.into(),
                operation: Operation::Replace,
                expected_version: expected,
                reason: reason.into(),
                body: None,
                attributes: Some(attributes),
                references: None,
                restore_version: None,
                sources: vec![],
                keeper: None,
            }],
        },
    )?;
    let content = proposal.changes[0].content.as_mut().expect("replacement");
    let mut parsed = Content::parse(content, "knowledge")?;
    if let Some(review) = &mut record.review {
        review.sha256 = governance::fingerprint(&mount.scope, name, &parsed);
    }
    parsed.metadata.attributes.insert(
        KEY.into(),
        serde_json::to_value(record).expect("record serializes"),
    );
    *content = parsed.render()?;
    proposal.id = proposal.digest();
    crate::upkeep::apply(memory, &proposal, false, None)?;
    Ok(proposal)
}

pub fn read(memory: &Memory, path: &str, policy: Policy) -> Result<Value, Error> {
    let (mount, name) = super::target(memory, path)?;
    let doc = memory
        .store
        .read(&mount.scope, name)?
        .filter(|d| !d.envelope.redacted)
        .ok_or_else(|| invalid("knowledge is missing or redacted"))?;
    let policy = policy.selected(memory.store.as_ref(), &mount.scope);
    let now = Stamp::now();
    let status = governance::status(&mount.scope, name, &doc.content, now);
    let included = policy.accepts(status);
    let mut result = json!({"reference":path,"scope":mount.scope,"path":name,"version":doc.envelope.version,
        "actor":doc.envelope.actor_value(),"status":status,"policy":policy,"included":included,"evaluated_at":now.utc().z()});
    if let Some(p) = memory.store.profile() {
        result["workspace_id"] = json!(p.workspace_id);
        result["source"] = json!(
            p.mounts
                .iter()
                .find(|m| m.scope == mount.scope)
                .map(|m| &m.source)
        );
    }
    if included {
        result["content"] = doc.content.into();
    }
    Ok(result)
}
