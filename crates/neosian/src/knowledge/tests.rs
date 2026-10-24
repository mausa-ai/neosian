use serde_json::json;

use super::model::{Content, Metadata};
use super::rules::Definition;

#[test]
fn marked_metadata_round_trips_without_changing_foreign_markdown_bytes() {
    let body = "---\r\ntitle: original\r\n---\r\n# Café\n<!-- text -->\n\r\n-->\r\n";
    let mut content = Content {
        metadata: Metadata::empty("notes"),
        body: body.into(),
    };
    content
        .metadata
        .attributes
        .insert("meaning".into(), json!("end --> and İ\n"));
    let encoded = content.render().unwrap();
    assert_eq!(encoded.matches("-->").count(), 3); // One delimiter and the original body.
    let read = Content::parse(&encoded, "ignored").unwrap();
    assert_eq!(read.body.as_bytes(), body.as_bytes());
    assert_eq!(read.metadata, content.metadata);
}

#[test]
fn unknown_formats_and_fields_are_refused_instead_of_downgraded_to_plain_text() {
    for text in [
        "<!-- neosian-knowledge:9\n{}\n-->\nretained",
        "<!-- neosian-knowledge:1\n{\"format\":1,\"collection\":\"notes\",\"policy\":true}\n-->\nretained",
    ] {
        assert!(Content::parse(text, "notes").is_err());
    }
}

#[test]
fn collection_rules_distinguish_integer_types_and_explain_missing_fields() {
    let definition = Definition::from_json(r#"{"format":1,"name":"Facts","schema":{"fields":{"count":{"type":"integer","required":true},"kind":{"type":"string","values":["fact"]}},"allow_unknown":false}}"#).unwrap();
    let attributes = json!({"count":1.5,"kind":"decision","other":true});
    let issues = definition.check("notes/a", attributes.as_object().unwrap());
    assert_eq!(issues.len(), 3);
    assert!(
        issues
            .iter()
            .any(|i| i.field == "count" && i.message == "expected integer")
    );
    assert_eq!(
        definition.check("notes/a", &Default::default())[0].message,
        "required field is missing"
    );
}
