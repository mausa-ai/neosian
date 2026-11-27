//! The parity corpus: literal conversations written for the handoff's
//! cases, each turn with a fixed time. Long tokens recur across turns so
//! link handles are numbered and shown: a URL in a prompt, a path in
//! prose, arguments and results, an id first seen in a result, a token
//! only a system message holds. Calls come several to a message, with
//! envelope results that succeed and fail, an orphan result, a call no
//! result answers, a result in two text blocks and one that is an image.

use serde_json::{Value, json};

use crate::conversation::message::from_json;
use crate::conversation::{Projection, Turn};
use crate::store::time::Stamp;

pub const URL: &str = "https://docs.example.com/neosian/records/continue/handoff/parity-check";
pub const PATH: &str = "/Users/dev/projects/neosian/crates/neosian/src/conversation/handoff.rs";
pub const ID: &str = "run_01ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghij";
pub const SYSTEM: &str = "/etc/neosian/system/instructions/for/agents/that/continue/work.md";

pub fn turn(id: &str, number: u64, messages: &[Value]) -> Turn {
    let minute = number % 60;
    let hour = 10 + number / 60;
    Turn {
        conversation_id: id.to_string(),
        turn: number,
        messages: messages.iter().map(|m| from_json(m).unwrap()).collect(),
        created_at: Stamp::parse(&format!("2026-11-27T{hour:02}:{minute:02}:00Z")).unwrap(),
        actor: Some(format!("claude-code:{}", 7 + number % 2)),
        redacted: false,
    }
}

fn call(id: &str, name: &str, arguments: Value) -> Value {
    json!({"id": id, "name": name, "arguments": arguments})
}

fn tool(id: &str, content: Value) -> Value {
    json!({"role": "tool", "content": content, "tool_call_id": id})
}

/// Four turns of calls, prose, envelopes and links.
pub fn calls(id: &str) -> Vec<Turn> {
    let envelope =
        json!({"success": true, "data": format!("total 8\n{PATH}\n{}", "x".repeat(300))});
    let failure = json!({"success": false, "error": format!("no match in {PATH}")});
    vec![
        turn(
            id,
            1,
            &[
                json!({"role": "user", "content": format!("Read {URL} and\n  fix   the handoff.")}),
                json!({"role": "assistant", "content": format!("Looking at {PATH} first."), "tool_calls": [
                    call("t1", "Read", json!({"file_path": PATH})),
                    call("t2", "Bash", json!({"command": "ls -la", "timeout": 30})),
                ]}),
                tool(
                    "t1",
                    json!(format!(
                        "fn render() {{}}\n// see {ID}\n{}",
                        "line\n".repeat(80)
                    )),
                ),
                tool("t2", json!(envelope.to_string())),
                json!({"role": "assistant", "content": "  Done: the handoff renders the frame.\n\nNext: views.  "}),
            ],
        ),
        turn(
            id,
            2,
            &[
                json!({"role": "user", "content": format!("Now café: check {ID} in {PATH} again")}),
                json!({"role": "assistant", "content": null, "tool_calls": [
                    call("t3", "Grep", json!({"pattern": "render", "path": PATH, "note": "naïve ☃ search"})),
                ]}),
                tool("t3", json!(failure.to_string())),
                json!({"role": "assistant", "content": "Nothing matched.\nThe path is right."}),
            ],
        ),
        turn(
            id,
            3,
            &[
                json!({"role": "system", "content": format!("Follow {SYSTEM} always.")}),
                json!({"role": "user", "content": format!("Where is {SYSTEM} used?")}),
                json!({"role": "assistant", "content": "Checking two things.", "tool_calls": [
                    call("t4", "Bash", json!({"command": format!("grep -r {SYSTEM} .")})),
                    call("t5", "Read", json!({"file_path": "/tmp/a"})),
                ]}),
                tool("t4", json!("")),
                tool("lost", json!("an orphan result")),
                json!({"role": "assistant", "content": "It is not used."}),
            ],
        ),
        turn(
            id,
            4,
            &[
                json!({"role": "user", "content": [{"type": "text", "text": "Look at this"}, {"type": "text", "text": format!("and {URL}")}]}),
                json!({"role": "assistant", "content": "One moment.", "tool_calls": [
                    call("t6", "Write", json!({"path": PATH, "text": "é".repeat(200)})),
                    call("t7", "Screenshot", json!({})),
                ]}),
                tool(
                    "t6",
                    json!([{"type": "text", "text": "wrote it"}, {"type": "text", "text": format!("to {PATH}")}]),
                ),
                tool(
                    "t7",
                    json!([{"type": "image", "media_type": "image/png", "data": "AAAA"}]),
                ),
                json!({"role": "assistant", "content": "", "tool_calls": [call("t8", "Bash", json!({"command": "true"}))]}),
                tool("t8", json!("ok")),
                json!({"role": "assistant", "content": format!("Both done; {ID} is fixed.")}),
            ],
        ),
    ]
}

/// Forty turns of long prompts: the oldest fold, projections stand for
/// turns 18 to 20 and turn 22 and overlap, turns 8 and 21 are erased.
pub fn folded(id: &str) -> (Vec<Turn>, Vec<Projection>) {
    let turns = (1..=40)
        .map(|number| {
            let prompt = format!("prompt {number} {URL} {}", "word ".repeat(240));
            let mut turn = turn(id, number, &[
                json!({"role": "user", "content": prompt}),
                json!({"role": "assistant", "content": null, "tool_calls": [call("c", "Bash", json!({"command": format!("echo {number}")}))]}),
                tool("c", json!(format!("{number}\n"))),
                json!({"role": "assistant", "content": format!("answer {number}: {}", "fine ".repeat(30))}),
            ]);
            if number == 8 || number == 21 {
                turn.messages.clear();
                turn.redacted = true;
            }
            turn
        })
        .collect();
    let entry = |turn, kind: &str, text: &str, span| Projection {
        turn,
        kind: kind.into(),
        text: text.into(),
        span,
    };
    let entries = vec![
        entry(6, "epoch", "turns 3 to 6 settled the plan", 4),
        entry(20, "epoch", "turns 18 to 20 settled the plan", 3),
        entry(19, "log", "turn 19 alone", 1),
        entry(22, "digest", &format!("turn twenty-two touched {PATH}"), 1),
        entry(39, "log", "the second newest, shown verbatim instead", 1),
    ];
    (turns, entries)
}

/// `calls` with a newest turn so long that the four before it are lines.
pub fn calls_as_lines(id: &str) -> Vec<Turn> {
    let mut turns = calls(id);
    let prompt = format!("wrap up {}", "z ".repeat(12_500));
    turns.push(turn(id, 5, &[json!({"role": "user", "content": prompt})]));
    turns
}

/// Prompts of a given size: `count` turns, each about `chars` long.
pub fn sized(id: &str, count: u64, chars: usize) -> Vec<Turn> {
    (1..=count)
        .map(|number| {
            let prompt = format!("{number}: {}", "abcd efgh ".repeat(chars / 10));
            turn(
                id,
                number,
                &[
                    json!({"role": "user", "content": prompt}),
                    json!({"role": "assistant", "content": format!("reply {number}")}),
                ],
            )
        })
        .collect()
}
