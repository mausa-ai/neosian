//! The payload reduction's tests: Claude Code's shapes, Cursor's
//! vocabulary, the span's messages in provider order.

use super::*;

fn payload(text: &str) -> Payload {
    parse_payload(text, "claude-code").unwrap()
}

#[test]
fn a_payload_is_reduced_to_its_record() {
    let (disposition, record) = reduce_payload(&payload(
        r#"{"session_id":"s","hook_event_name":"UserPromptSubmit","user_prompt":"hi"}"#,
    ));
    assert_eq!(disposition, "spooled");
    assert_eq!(
        Value::Object(record.unwrap()),
        json!({"kind": "prompt", "text": "hi"})
    );
    let (_, tool) = reduce_payload(&payload(
        r#"{"session_id":"s","hook_event_name":"PostToolUse","tool_name":"Read","tool_input":{"b":1,"a":2},"tool_use_id":"t1","tool_response":{"type":"text","text":"ok"}}"#,
    ));
    assert_eq!(
        Value::Object(tool.unwrap()).to_string(),
        r#"{"kind":"tool","id":"t1","name":"Read","input":{"b":1,"a":2},"response":"ok"}"#
    );
    let (_, odd) = reduce_payload(&payload(
        r#"{"session_id":"s","hook_event_name":"PostToolUse","tool_input":"x","tool_response":{"b":[1,"é"],"a":null}}"#,
    ));
    assert_eq!(
        Value::Object(odd.unwrap()),
        json!({"kind": "tool", "id": null, "name": "tool", "input": {"input": "x"}, "response": "{\"a\": null, \"b\": [1, \"é\"]}"})
    );
    assert_eq!(
        reduce_payload(&payload(
            r#"{"session_id":"s","hook_event_name":"PostToolUse","agent_id":"sub"}"#
        )),
        ("skipped", None)
    );
    let (_, stop) = reduce_payload(&payload(
        r#"{"session_id":"s","hook_event_name":"Stop","last_assistant_message":"done"}"#,
    ));
    assert_eq!(
        Value::Object(stop.unwrap()),
        json!({"kind": "stop", "text": "done"})
    );
    assert_eq!(
        reduce_payload(&payload(
            r#"{"session_id":"s","hook_event_name":"Notification"}"#
        )),
        ("ignored", None)
    );
    for (text, why) in [
        ("[]", "stdin is not a JSON object"),
        (
            r#"{"hook_event_name":"Stop"}"#,
            "the payload carries no session_id",
        ),
    ] {
        assert_eq!(parse_payload(text, "claude-code").unwrap_err(), why);
    }
    assert!(
        parse_payload("{", "claude-code")
            .unwrap_err()
            .starts_with("stdin is not JSON: ")
    );
}

#[test]
fn cursor_speaks_its_own_vocabulary() {
    let native = parse_payload(
        r#"{"conversation_id":"c","cursor_version":"1","hook_event_name":"postToolUse","tool_name":"Shell","tool_output":"/work"}"#,
        "cursor",
    )
    .unwrap();
    assert_eq!(native["session_id"], "c");
    assert_eq!(native["hook_event_name"], TOOL_EVENT);
    assert_eq!(native["tool_response"], "/work");
    assert_eq!(native["status"], "completed");
    let (_, stop) = reduce_payload(
        &parse_payload(
            r#"{"conversation_id":"c","hook_event_name":"stop","status":"failed"}"#,
            "cursor",
        )
        .unwrap(),
    );
    assert_eq!(
        Value::Object(stop.unwrap()),
        json!({"kind": "stop", "text": "", "status": "failed"})
    );
    let imported = parse_payload(
        r#"{"session_id":"x","conversation_id":"c","cursor_version":"1","hook_event_name":"Stop"}"#,
        "claude-code",
    )
    .unwrap();
    assert_eq!(imported["session_id"], "c");
    assert_eq!(reduce_payload(&imported), ("ignored", None));
}

#[test]
fn a_span_becomes_messages_in_provider_order() {
    let records: Vec<Record> = [
        json!({"kind": "prompt", "text": "add a test"}),
        json!({"kind": "tool", "id": null, "name": "Read", "input": {"f": 1}, "response": "def"}),
        json!({"kind": "tool", "id": "t2", "name": "Bash", "input": {}, "response": ""}),
        json!({"kind": "stop", "text": ""}),
        json!({"kind": "assistant", "text": "done"}),
    ]
    .into_iter()
    .map(|value| value.as_object().unwrap().clone())
    .collect();
    let messages = messages_of(&records);
    let roles: Vec<Role> = messages.iter().map(|m| m.role).collect();
    assert_eq!(
        roles,
        [
            Role::User,
            Role::Assistant,
            Role::Tool,
            Role::Assistant,
            Role::Tool,
            Role::Assistant
        ]
    );
    assert_eq!(messages[1].tool_calls[0].id, "round-1");
    assert_eq!(messages[2].tool_call_id.as_deref(), Some("round-1"));
    assert_eq!(messages[3].tool_calls[0].id, "t2");
    assert_eq!(last_prompt(&records).as_deref(), Some("add a test"));
    assert!(messages_of(&records[3..4]).is_empty() && last_prompt(&records[1..]).is_none());
    let long = "x".repeat(TOOL_RESULT_CHARS + 10);
    let kept = head_and_tail(&long, TOOL_RESULT_CHARS);
    assert!(kept.contains("\n… [10 chars omitted of 1048586] …\n"));
    assert_eq!(
        len(&kept),
        TOOL_RESULT_CHARS + len("\n… [10 chars omitted of 1048586] …\n")
    );
}
