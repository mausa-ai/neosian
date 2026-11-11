use super::*;
use crate::store::{Document, envelope::Envelope, time::Stamp};

fn request(query: &str) -> Request {
    serde_json::from_value(json!({"query":query})).unwrap()
}

fn document(content: &str) -> Document {
    Document {
        content: content.into(),
        envelope: Envelope {
            version: 3,
            created_at: Stamp::parse("2026-10-24T10:00:00Z").unwrap(),
            updated_at: Stamp::parse("2026-10-24T10:00:00Z").unwrap(),
            actor: Some("cli:author".into()),
            redacted: false,
            extra: vec![],
        },
    }
}

#[test]
fn unicode_expansion_keeps_excerpt_offsets_in_original_characters() {
    let body = format!("{}İSTANBUL {}", "é".repeat(300), "tail ".repeat(80));
    let q = request("i\u{307}stanbul");
    let page = scan(
        "user:test",
        &q,
        [Ok(("a".into(), document(&body)))].into_iter(),
    )
    .unwrap();
    let excerpt = &page.hits[0].excerpt;
    assert_eq!(excerpt.offset, 260);
    assert_eq!(excerpt.text.chars().count(), 240);
    assert!(excerpt.text.contains("İSTANBUL"));
    assert_eq!(
        excerpt.text,
        body.chars().skip(260).take(240).collect::<String>()
    );
}

#[test]
fn terms_span_selected_fields_but_never_object_keys_or_unselected_values() {
    let mut q = request("release blue ship");
    q.fields.push("metadata".into());
    q.metadata_keys.push("tags".into());
    let mut doc = document("Ship the change");
    doc.envelope.extra = vec![
        "tags: {\"hidden-key\": [42, true, \"Blue\"]}".into(),
        "secret: private".into(),
    ];
    let found = matching::find(
        "user:test",
        "release/a",
        &doc,
        &q,
        &matching::terms(&q.query),
    )
    .unwrap();
    assert_eq!(found.unwrap().1, ["content", "metadata", "path"]);
    for query in ["hidden-key", "42", "true", "private"] {
        q.query = query.into();
        assert!(
            matching::find("user:test", "release/a", &doc, &q, &matching::terms(query))
                .unwrap()
                .is_none()
        );
    }
}

#[test]
fn cursor_changes_page_size_but_cannot_change_scope_or_query() {
    let mut q = request("needle");
    q.cursor = Some(q.cursor_for(&json!("user:test"), "memory-search-v1", "notes/a"));
    q.limit = 1;
    assert_eq!(q.scope_after("user:test").unwrap(), "notes/a");
    assert!(q.scope_after("user:other").is_err());
    q.query = "other".into();
    assert!(q.scope_after("user:test").is_err());
}

fn ranked_hit(path: &str, score: Option<u64>, updated: &str) -> Hit {
    Hit {
        scope: "user:test".into(),
        path: path.into(),
        version: 1,
        created_at: "2026-10-24T10:00:00Z".into(),
        updated_at: updated.into(),
        actor: None,
        matched_fields: vec!["content".into()],
        excerpt: Excerpt {
            field: "content".into(),
            metadata_key: None,
            offset: 0,
            text: "needle".into(),
        },
        score,
        reference: None,
        source: None,
        workspace_id: None,
    }
}

#[test]
fn backend_pages_must_follow_the_requested_order_and_bounds() {
    let mut q = request("needle");
    q.order = Some(Order::Relevance);
    let page = |hits: Vec<Hit>, order: Option<Order>| Page {
        hits,
        next_cursor: None,
        order,
        fallback: None,
    };
    let t = "2026-10-24T10:00:00Z";
    let good = page(
        vec![
            ranked_hit("b", Some(9), t),
            ranked_hit("a", Some(3), t),
            ranked_hit("c", Some(3), t),
        ],
        Some(Order::Relevance),
    );
    assert!(q.check_page("user:test", &good).is_ok());
    for bad in [
        page(
            vec![ranked_hit("a", Some(3), t), ranked_hit("b", Some(9), t)],
            Some(Order::Relevance),
        ),
        page(
            vec![ranked_hit("c", Some(3), t), ranked_hit("a", Some(3), t)],
            Some(Order::Relevance),
        ),
        page(vec![ranked_hit("a", None, t)], Some(Order::Relevance)),
        page(vec![ranked_hit("a", Some(3), t)], None),
        page(
            vec![ranked_hit("a", Some(3), t), ranked_hit("a", Some(2), t)],
            Some(Order::Relevance),
        ),
    ] {
        assert!(q.check_page("user:test", &bad).is_err());
    }
    q.order = Some(Order::Newest);
    q.updated_since = Some("2026-10-24T10:00:00Z".into());
    let newer = "2026-10-25T10:00:00Z";
    let newest = page(
        vec![ranked_hit("z", None, newer), ranked_hit("a", None, t)],
        Some(Order::Newest),
    );
    assert!(q.check_page("user:test", &newest).is_ok());
    let early = page(
        vec![ranked_hit("a", None, "2026-10-23T10:00:00Z")],
        Some(Order::Newest),
    );
    assert!(q.check_page("user:test", &early).is_err());
    let reversed = page(
        vec![ranked_hit("a", None, t), ranked_hit("z", None, newer)],
        Some(Order::Newest),
    );
    assert!(q.check_page("user:test", &reversed).is_err());
}
