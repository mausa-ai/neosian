//! History ranking version 1 over the wire: the request carries the order and
//! bounds, and the answer is validated before any turn is trusted (§40.43). A
//! redacted turn has no text, so it can never be a candidate.

use serde_json::json;

use super::RemoteStore;
use super::codec;
use crate::conversation::rank::{Order, Ranked, Request};
use crate::conversation::{Error, Turn};
use crate::store::error::MemoryError;

impl RemoteStore {
    pub(super) fn supports_history_ranking(&self) -> bool {
        self.capabilities["history_ranking"]["version"] == 1
    }

    pub(super) fn rank_turns_wire(&self, request: &Request) -> Result<Vec<Ranked>, Error> {
        if !self.supports_history_ranking() {
            return Err(Error::Memory(MemoryError::configuration(
                "server does not support history-ranking version 1",
            )));
        }
        let (since, before) = request.bounds().map_err(Error::Value)?;
        let mut body = json!({
            "query": request.terms.join(" "),
            "conversations": request.ids,
            "limit": request.limit,
            "order": request.order.name(),
        });
        if let Some(since) = &request.since {
            body["since"] = json!(since);
        }
        if let Some(before) = &request.before {
            body["before"] = json!(before);
        }
        let data = self.call("conversation/search_turns", body)?;
        let bad = || Error::from(codec::malformed("conversation/search_turns"));
        let turns: Vec<Turn> = codec::array(&data, "turns")?
            .iter()
            .map(codec::turn)
            .collect::<Result<_, _>>()?;
        let scores: Vec<Option<u64>> = match request.order {
            Order::Relevance => codec::array(&data, "scores")?
                .iter()
                .map(|score| score.as_u64().map(Some).ok_or_else(bad))
                .collect::<Result<_, _>>()?,
            Order::Newest => {
                if data.get("scores").is_some() {
                    return Err(bad());
                }
                vec![None; turns.len()]
            }
        };
        let key = |turn: &Turn, score: Option<u64>| {
            (
                std::cmp::Reverse(score),
                std::cmp::Reverse((
                    turn.created_at.instant(),
                    turn.conversation_id.clone(),
                    turn.turn,
                )),
            )
        };
        let outside = |turn: &Turn| {
            let at = turn.created_at.instant();
            since.is_some_and(|s| at < s)
                || before.is_some_and(|b| at >= b)
                || request
                    .ids
                    .as_ref()
                    .is_some_and(|ids| !ids.contains(&turn.conversation_id))
        };
        if data["order"] != json!(request.order.name())
            || turns.len() > request.limit
            || scores.len() != turns.len()
            || turns.iter().any(outside)
            || turns.iter().any(|turn| turn.redacted)
            || turns
                .iter()
                .zip(&scores)
                .collect::<Vec<_>>()
                .windows(2)
                .any(|w| key(w[0].0, *w[0].1) >= key(w[1].0, *w[1].1))
        {
            return Err(bad());
        }
        Ok(turns.into_iter().zip(scores).collect())
    }
}
