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
