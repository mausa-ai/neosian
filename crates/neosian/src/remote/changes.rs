use super::{RemoteStore, codec};
use crate::changes::model::{Page, Pruned, Request, invalid};
use crate::store::{error::Error, grammar::parse_scope};
use serde_json::json;

impl RemoteStore {
    pub(super) fn changes_read(&self, scope: &str, request: &Request) -> Result<Page, Error> {
        parse_scope(scope)?;
        request.validate()?;
        self.require_changes()?;
        let mut payload = serde_json::to_value(request).expect("change request serializes");
        payload["scope"] = scope.into();
        let page = serde_json::from_value(self.call("memory/changes", payload)?)
            .map_err(|_| codec::malformed("memory/changes"))?;
        request.check_page(scope, &page)?;
        Ok(page)
    }

    pub(super) fn changes_prune(
        &self,
        scope: &str,
        through: &str,
        write: bool,
    ) -> Result<Pruned, Error> {
        parse_scope(scope)?;
        if through.is_empty() || through.len() > 2048 {
            return Err(invalid("invalid retention cursor size"));
        }
        self.require_changes()?;
        let result: Pruned = serde_json::from_value(self.call(
            "memory/prune_changes",
            json!({"scope":scope,"through":through,"write":write}),
        )?)
        .map_err(|_| codec::malformed("memory/prune_changes"))?;
        if result.format != 1
            || result.scope != scope
            || result.write != write
            || result.removed > 10_000
            || result.earliest_cursor.is_empty()
            || result.earliest_cursor.len() > 2048
        {
            return Err(codec::malformed("memory/prune_changes"));
        }
        Ok(result)
    }

    fn require_changes(&self) -> Result<(), Error> {
        if self.capabilities["change_feed"]["version"] != 1 {
            return Err(invalid("server does not support change-feed version 1"));
        }
        Ok(())
    }
}
