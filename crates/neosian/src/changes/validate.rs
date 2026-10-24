use super::model::{Page, Request, invalid, uuid};
use crate::store::error::Error;

impl Request {
    pub fn check_page(&self, scope: &str, page: &Page) -> Result<(), Error> {
        let bad = || invalid("malformed change-feed response");
        if page.format != 1
            || page.scope != scope
            || page.events.len() > self.limit
            || page
                .gap
                .as_deref()
                .is_some_and(|s| !matches!(s, "untracked" | "retention" | "stream_reset"))
            || page
                .cursor
                .iter()
                .chain(&page.earliest_cursor)
                .chain(&page.head_cursor)
                .any(|c| c.is_empty() || c.len() > 2048)
            || (page.gap.is_some() && (!page.events.is_empty() || page.has_more))
            || (page.has_more && page.events.is_empty())
        {
            return Err(bad());
        }
        match &page.stream {
            Some(stream)
                if uuid(stream)
                    && page.cursor.is_some()
                    && page.head_cursor.is_some()
                    && page.earliest_cursor.is_some()
                    && page.gap.as_deref() != Some("untracked") => {}
            None if page.events.is_empty()
                && page.cursor.is_none()
                && page.head_cursor.is_none()
                && page.earliest_cursor.is_none()
                && matches!(page.gap.as_deref(), Some("untracked" | "stream_reset")) => {}
            _ => return Err(bad()),
        }
        let mut last = None;
        for event in &page.events {
            event.validate()?;
            if event.scope != scope
                || Some(&event.stream) != page.stream.as_ref()
                || last.is_some_and(|n| event.sequence != n + 1)
            {
                return Err(bad());
            }
            last = Some(event.sequence);
        }
        Ok(())
    }
}
