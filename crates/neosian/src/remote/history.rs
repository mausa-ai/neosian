//! History ranking version 1 over the wire: the request carries the order and
//! bounds, and the answer is validated before any turn is trusted (§40.43). A
//! redacted turn has no text, so it can never be a candidate. The client
//! sends one unpaged request (§40.46, #417).

use serde_json::{Value, json};

use super::RemoteStore;
use super::codec;
use crate::conversation::page::Visit;
use crate::conversation::rank::{Bounds, Order, Ranked, Request};
use crate::conversation::{Error, Turn};
use crate::store::error::MemoryError;

type Place = (
    std::cmp::Reverse<Option<u64>>,
    std::cmp::Reverse<(i64, String, u64)>,
);

/// The checks every page of one ranked answer passes, carried across pages:
/// the echoed order, scores, bounds, selection, limit and strict order.
struct Checked<'a> {
    request: &'a Request,
    bounds: Bounds,
    previous: Option<Place>,
    total: usize,
}

impl Checked<'_> {
    fn page(&mut self, data: &Value) -> Result<Vec<Ranked>, Error> {
        let request = self.request;
        let bad = || Error::from(codec::malformed("conversation/search_turns"));
        let turns: Vec<Turn> = codec::array(data, "turns")?
            .iter()
            .map(codec::turn)
            .collect::<Result<_, _>>()?;
        let scores: Vec<Option<u64>> = match request.order {
            Order::Relevance => codec::array(data, "scores")?
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
        let (since, before) = self.bounds;
        let outside = |turn: &Turn| {
            let at = turn.created_at.instant();
            since.is_some_and(|s| at < s)
                || before.is_some_and(|b| at >= b)
                || request
                    .ids
                    .as_ref()
                    .is_some_and(|ids| !ids.contains(&turn.conversation_id))
        };
        self.total += turns.len();
        if data["order"] != json!(request.order.name())
            || self.total > request.limit
            || scores.len() != turns.len()
            || turns.iter().any(outside)
            || turns.iter().any(|turn| turn.redacted)
        {
            return Err(bad());
        }
        for (turn, score) in turns.iter().zip(&scores) {
            let place = (
                std::cmp::Reverse(*score),
                std::cmp::Reverse((
                    turn.created_at.instant(),
                    turn.conversation_id.clone(),
                    turn.turn,
                )),
            );
            if self
                .previous
                .as_ref()
                .is_some_and(|previous| *previous >= place)
            {
                return Err(bad());
            }
            self.previous = Some(place);
        }
        Ok(turns.into_iter().zip(scores).collect())
    }
}

impl RemoteStore {
    pub(super) fn supports_history_ranking(&self) -> bool {
        self.capabilities["history_ranking"]["version"] == 1
    }

    pub(super) fn rank_turns_wire(&self, request: &Request) -> Result<Vec<Ranked>, Error> {
        let mut ranked = Vec::new();
        self.rank_turns_wire_each(request, &mut |turn, score| {
            ranked.push((turn, score));
            Ok(true)
        })?;
        Ok(ranked)
    }

    pub(super) fn rank_turns_wire_each(
        &self,
        request: &Request,
        visit: &mut Visit,
    ) -> Result<(), Error> {
        if !self.supports_history_ranking() {
            return Err(Error::Memory(MemoryError::configuration(
                "server does not support history-ranking version 1",
            )));
        }
        let bounds = request.bounds().map_err(Error::Value)?;
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
        let mut checked = Checked {
            request,
            bounds,
            previous: None,
            total: 0,
        };
        // One unpaged request, as 2.16.0 sent: a size page would repeat the
        // server's scan (#417).
        for (turn, score) in checked.page(&self.call("conversation/search_turns", body)?)? {
            if !visit(turn, score)? {
                break;
            }
        }
        Ok(())
    }
}
