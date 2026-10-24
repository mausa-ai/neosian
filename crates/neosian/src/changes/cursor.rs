use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde_json::json;

use super::model::{Head, Page, Request, invalid, uuid};
use crate::store::error::Error;

pub fn encode(scope: &str, head: &Head, sequence: u64) -> String {
    URL_SAFE_NO_PAD.encode(json!([1, scope, head.stream, sequence]).to_string())
}

pub fn decode(scope: &str, text: &str) -> Result<(String, u64), Error> {
    let bad = || invalid("invalid change cursor for this scope");
    if text.is_empty() || text.len() > 2048 {
        return Err(bad());
    }
    let bytes = URL_SAFE_NO_PAD.decode(text).map_err(|_| bad())?;
    let (format, target, stream, sequence): (u32, String, String, u64) =
        serde_json::from_slice(&bytes).map_err(|_| bad())?;
    if format != 1 || target != scope || !uuid(&stream) || sequence > i64::MAX as u64 {
        return Err(bad());
    }
    Ok((stream, sequence))
}

/// Prepare the bounded range. A gap returns no events and a resumable floor.
pub fn page(
    scope: &str,
    head: Option<&Head>,
    request: &Request,
) -> Result<(Page, u64, u64), Error> {
    request.validate()?;
    let after = request
        .cursor
        .as_deref()
        .map(|s| decode(scope, s))
        .transpose()?;
    let mut page = Page {
        format: 1,
        scope: scope.into(),
        stream: None,
        events: vec![],
        cursor: None,
        head_cursor: None,
        earliest_cursor: None,
        has_more: false,
        gap: None,
    };
    let Some(head) = head else {
        page.gap = Some(
            if after.is_some() {
                "stream_reset"
            } else {
                "untracked"
            }
            .into(),
        );
        return Ok((page, 0, 0));
    };
    head.validate()?;
    page.stream = Some(head.stream.clone());
    page.earliest_cursor = Some(encode(scope, head, head.floor));
    page.head_cursor = Some(encode(scope, head, head.sequence));
    let position = match after {
        Some((stream, _)) if stream != head.stream => {
            page.gap = Some("stream_reset".into());
            head.floor
        }
        Some((_, position)) if position < head.floor => {
            page.gap = Some("retention".into());
            head.floor
        }
        Some((_, position)) if position > head.sequence => {
            return Err(invalid("change cursor is ahead of the stream"));
        }
        Some((_, position)) => position,
        None if head.floor > 0 => {
            page.gap = Some("retention".into());
            head.floor
        }
        None => 0,
    };
    let end = if page.gap.is_some() {
        position
    } else {
        head.sequence
            .min(position.saturating_add(request.limit as u64))
    };
    page.cursor = Some(encode(scope, head, end));
    page.has_more = page.gap.is_none() && end < head.sequence;
    Ok((page, position, end))
}

pub fn through(scope: &str, head: &Head, cursor: &str) -> Result<u64, Error> {
    let (stream, sequence) = decode(scope, cursor)?;
    if stream != head.stream || sequence < head.floor || sequence > head.sequence {
        return Err(invalid(
            "retention cursor is outside the current retained stream",
        ));
    }
    if sequence - head.floor > 10_000 {
        return Err(invalid("prune at most 10000 change events per request"));
    }
    Ok(sequence)
}
