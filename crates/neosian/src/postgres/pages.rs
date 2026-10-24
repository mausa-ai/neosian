//! Keyset pages run the Python store's bounded SQL, including trail ids.

use super::{PostgresStore, memory, rows, sql};
use crate::remote::codec;
use crate::store::cursor::{self, Kind};
use crate::store::error::Error;
use crate::store::grammar::parse_scope;
use crate::store::journal::{Act, Row};
use crate::store::pages::{page_limit, paged};
use crate::store::time::Stamp;
use crate::store::{Entry, Page};
use serde_json::{Value, json};

fn parts(method: &str, cursor: Option<&str>, kinds: &[Kind]) -> Result<Vec<Value>, Error> {
    match cursor {
        Some(cursor) => cursor::decode(method, cursor, kinds).map_err(Error::Value),
        None => Ok(vec![Value::Null; kinds.len()]),
    }
}

impl PostgresStore {
    pub fn list_documents_page(
        &self,
        scope: &str,
        prefix: &str,
        cursor: Option<&str>,
        limit: i64,
    ) -> Result<Page<Entry>, Error> {
        parse_scope(scope)?;
        let limit = page_limit(limit)?;
        let key = parts("list", cursor, &[Kind::Str])?;
        let rows = self.fetch(sql!("pageable/list_documents"), json!({"scope":scope,"pattern":format!("{}%", rows::escaped(prefix)),"path":key[0],"limit":limit+1}))?;
        let entries = rows
            .iter()
            .map(|row| memory::entry(scope, row))
            .collect::<Result<_, _>>()?;
        Ok(paged(entries, limit, |entry| {
            cursor::encode("list", &[json!(entry.path)])
        }))
    }

    pub fn versions_page(
        &self,
        scope: &str,
        path: &str,
        cursor: Option<&str>,
        limit: i64,
    ) -> Result<Page<Row>, Error> {
        rows::address(scope, path)?;
        let limit = page_limit(limit)?;
        let key = parts("versions", cursor, &[Kind::Int])?;
        let rows = self.fetch(
            sql!("pageable/read_versions"),
            json!({"scope":scope,"path":path,"version":key[0],"limit":limit+1}),
        )?;
        let entries = rows
            .iter()
            .map(|row| memory::version(scope, row))
            .collect::<Result<_, _>>()?;
        Ok(paged(entries, limit, |row| {
            cursor::encode("versions", &[json!(row.version)])
        }))
    }

    pub fn history_page(
        &self,
        scope: &str,
        since: Option<Stamp>,
        cursor: Option<&str>,
        limit: i64,
    ) -> Result<Page<Row>, Error> {
        parse_scope(scope)?;
        let limit = page_limit(limit)?;
        let key = parts("history", cursor, &[Kind::Instant, Kind::Str, Kind::Int])?;
        let rows = self.fetch(sql!("pageable/read_history"), json!({"scope":scope,"since":since.map(Stamp::z),"at":key[0],"path":key[1],"version":key[2],"limit":limit+1}))?;
        let entries = rows
            .iter()
            .map(|row| memory::version(scope, row))
            .collect::<Result<_, _>>()?;
        Ok(paged(entries, limit, |row| {
            cursor::encode(
                "history",
                &[
                    json!(cursor::instant(row.created_at)),
                    json!(row.path),
                    json!(row.version),
                ],
            )
        }))
    }

    pub fn redactions_page(
        &self,
        scope: &str,
        since: Option<Stamp>,
        cursor: Option<&str>,
        limit: i64,
    ) -> Result<Page<Act>, Error> {
        parse_scope(scope)?;
        let limit = page_limit(limit)?;
        let key = parts("trail", cursor, &[Kind::Instant, Kind::Int])?;
        let rows = self.fetch(sql!("pageable/read_redactions"), json!({"scope":scope,"since":since.map(Stamp::z),"at":key[0],"id":key[1],"limit":limit+1}))?;
        let entries = rows
            .iter()
            .map(|row| Ok((codec::redaction(row)?, codec::number(row, "id")?)))
            .collect::<Result<_, Error>>()?;
        let page = paged(entries, limit, |(act, id)| {
            cursor::encode(
                "trail",
                &[json!(cursor::instant(act.created_at)), json!(id)],
            )
        });
        Ok(Page {
            items: page.items.into_iter().map(|(act, _)| act).collect(),
            next_cursor: page.next_cursor,
        })
    }
}
