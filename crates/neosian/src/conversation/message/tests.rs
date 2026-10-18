use serde_json::json;

use super::*;

#[test]
fn a_message_reads_as_the_codec_wrote_it() {
    let message = from_json(&json!({
        "role": "assistant", "content": null, "reasoning": null,
        "tool_calls": [{"id": "t1", "name": "Read", "arguments": {"b": 1, "a": 2}}],
        "tool_call_id": null
    }))
    .unwrap();
    assert_eq!(message.role, Role::Assistant);
    assert_eq!(text_of(&message), "");
    assert_eq!(
        message.tool_calls[0].arguments.to_string(),
        r#"{"b":1,"a":2}"#
    );
    let blocks = from_json(&json!({
        "role": "user",
        "content": [
            {"type": "text", "text": "one"},
            {"type": "image", "url": "https://x/y.png"},
            {"type": "text", "text": "two"}
        ]
    }))
    .unwrap();
    assert_eq!(text_of(&blocks), "one\ntwo");
    let tool = from_json(&json!({"role": "tool", "content": "ok", "tool_call_id": "t1"}));
    assert_eq!(tool.unwrap().tool_call_id.as_deref(), Some("t1"));
    let bare = from_json(&json!({"role": "user", "tool_calls": "", "tool_call_id": ""})).unwrap();
    assert!(bare.tool_calls.is_empty() && bare.tool_call_id.is_none());
}

#[test]
fn every_block_and_the_extras_round_trip() {
    let sent = json!({
        "role": "user",
        "content": [
            {"type": "text", "text": "hi"},
            {"type": "image", "data": "ab cd\nef", "media_type": "image/png"},
            {"type": "document", "url": "https://x/y.pdf", "junk": 1},
            {"type": "compaction", "content": "folded"}
        ],
        "reasoning": "because",
        "tool_calls": [{"id": "c1", "name": "m", "extra": {"k": 1}}],
        "extra": {"provider": "x"}
    });
    let encoded = to_json(&from_json(&sent).unwrap());
    assert_eq!(
        encoded,
        json!({
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image", "media_type": "image/png", "data": "abcdef", "url": null},
                {"type": "document", "media_type": null, "data": null, "url": "https://x/y.pdf"},
                {"type": "compaction", "content": "folded", "encrypted_content": null}
            ],
            "reasoning": "because",
            "tool_calls": [{"id": "c1", "name": "m", "arguments": {}, "extra": {"k": 1}}],
            "tool_call_id": null,
            "extra": {"provider": "x"}
        })
    );
    let plain = to_json(&from_json(&json!({"role": "user", "extra": {}})).unwrap());
    assert!(plain.get("extra").is_none());
    assert_eq!(plain["reasoning"], Value::Null);
}

#[test]
fn what_the_codec_refuses_is_named() {
    let refused = |data: Value| from_json(&data).unwrap_err();
    assert_eq!(refused(json!({"content": "x"})), Undecodable::Key("role"));
    assert_eq!(
        refused(json!({"role": "robot"})),
        Undecodable::Value("'robot' is not a valid Role".to_string())
    );
    assert_eq!(
        refused(json!({"role": 5})),
        Undecodable::Value("5 is not a valid Role".to_string())
    );
    assert_eq!(
        refused(json!({"role": "user", "content": [{"type": "video"}]})),
        Undecodable::Value("Unknown content block type: 'video'".to_string())
    );
    assert_eq!(
        refused(json!({"role": "user", "content": [{}]})),
        Undecodable::Value("Unknown content block type: None".to_string())
    );
    assert_eq!(
        refused(json!({"role": "user", "content": [{"type": "text"}]})),
        Undecodable::Key("text")
    );
    assert_eq!(
        refused(json!({"role": "user", "content": [{"type": "image", "data": "abc"}]})),
        Undecodable::Value("ImageBlock requires media_type with base64 data".to_string())
    );
    assert_eq!(
        refused(
            json!({"role": "user", "content": [{"type": "document", "data": "a", "url": "u"}]})
        ),
        Undecodable::Value("DocumentBlock requires exactly one of 'data' or 'url'".to_string())
    );
    assert_eq!(
        refused(json!({"role": "assistant", "tool_calls": [{"id": "t1"}]})),
        Undecodable::Key("name")
    );
    assert_eq!(
        refused(json!({"role": "assistant", "tool_calls": [{"id": 1, "name": "x"}]})),
        Undecodable::Value("tool call parameter 'id' must be a string".to_string())
    );
    assert_eq!(
        refused(
            json!({"role": "assistant", "tool_calls": [{"id": "t", "name": "x", "arguments": "{}"}]})
        ),
        Undecodable::Value("tool call parameter 'arguments' must be an object".to_string())
    );
    for data in [
        json!([]),
        json!({"role": "user", "content": 7}),
        json!({"role": "user", "content": [5]}),
        json!({"role": "assistant", "tool_calls": "x"}),
        json!({"role": "assistant", "tool_calls": [5]}),
        json!({"role": "tool", "tool_call_id": 5}),
    ] {
        assert!(matches!(refused(data), Undecodable::Type(_)));
    }
}
