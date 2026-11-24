//! Read winning keys in the candidate snapshot. Grouping by turn keeps a
//! legacy body alive only once and bounds call-name lookups by the winners.
use std::collections::{BTreeMap, HashMap, hash_map::Entry};
use std::rc::Rc;

use super::{Context, Location, legacy, native, record_read};
use crate::conversation::turn_records::{Kind, RecordHit, TurnRecord, record_excerpt};
use crate::conversation::{Error, Refused};
use crate::store::time::Stamp;

type Wanted = (usize, Location, Option<u64>);

pub(super) async fn hits(
    cx: &Context<'_, '_>,
    winners: Vec<(Location, Option<u64>)>,
) -> Result<Vec<RecordHit>, Error> {
    let mut result = Vec::with_capacity(winners.len());
    let mut turns = BTreeMap::<(Rc<str>, u64, bool), Vec<Wanted>>::new();
    for (at, (key, score)) in winners.into_iter().enumerate() {
        turns
            .entry((key.id.clone(), key.turn, key.legacy))
            .or_default()
            .push((at, key, score));
    }
    for ((id, turn, whole), wanted) in turns {
        if whole {
            let (records, actor) = legacy::records(cx, &id, turn).await?;
            let picks = wanted
                .iter()
                .map(|(at, key, score)| {
                    let record = usize::try_from(key.seq)
                        .ok()
                        .and_then(|seq| records.get(seq))
                        .filter(|record| record.seq == key.seq)
                        .ok_or_else(|| {
                            Refused::format_unsupported(
                                &id,
                                &format!("turn {turn}: a record the search found is gone"),
                            )
                        })?;
                    Ok::<_, Error>((*at, key, *score, record))
                })
                .collect::<Result<Vec<_>, _>>()?;
            let mut names: HashMap<&str, Option<&str>> = picks
                .iter()
                .filter_map(|(_, _, _, r)| result_call(r).map(|id| (id, None)))
                .collect();
            if !names.is_empty() {
                for record in &records {
                    if record.kind == Kind::ToolCall
                        && let Some(slot) =
                            record.call_id.as_deref().and_then(|id| names.get_mut(id))
                        && slot.is_none()
                    {
                        *slot = record.name.as_deref();
                    }
                }
            }
            for (at, key, score, record) in picks {
                let name = result_call(record).and_then(|id| names.get(id).copied().flatten());
                result.push((at, hit(cx, key, score, record, actor.as_deref(), name)));
            }
        } else {
            let mut details = HashMap::new();
            for (at, key, score) in wanted {
                let record =
                    record_read::one_record(cx.tx, cx.statements, &id, turn, key.seq, cx.budget)
                        .await?;
                let (actor, name) = match details.entry(result_call(&record).map(str::to_owned)) {
                    Entry::Occupied(entry) => entry.into_mut(),
                    Entry::Vacant(entry) => {
                        let found = native::details(cx, &id, turn, entry.key().as_deref()).await?;
                        entry.insert(found)
                    }
                };
                result.push((
                    at,
                    hit(cx, &key, score, &record, actor.as_deref(), name.as_deref()),
                ));
            }
        }
    }
    result.sort_unstable_by_key(|(at, _)| *at);
    Ok(result.into_iter().map(|(_, hit)| hit).collect())
}

fn result_call(record: &TurnRecord) -> Option<&str> {
    record
        .call_id
        .as_deref()
        .filter(|_| record.kind == Kind::ToolResult)
}

fn hit(
    cx: &Context<'_, '_>,
    key: &Location,
    score: Option<u64>,
    record: &TurnRecord,
    actor: Option<&str>,
    result_name: Option<&str>,
) -> RecordHit {
    let name = match record.kind {
        Kind::ToolCall => record.name.clone(),
        Kind::ToolResult => result_name.map(str::to_owned),
        _ => None,
    };
    RecordHit {
        conversation_id: key.id.to_string(),
        turn: key.turn,
        seq: record.seq,
        message: record.message,
        kind: record.kind,
        call_id: record.call_id.clone(),
        name,
        created_at: Stamp::from_micros(key.created),
        actor: actor.map(str::to_owned),
        size: record.size,
        excerpt: record_excerpt(&record.text, &cx.search.terms),
        score,
    }
}
