//! The wire (`mcp/server/stdio.py` and the SDK's dispatcher, as the
//! Python server answers): one JSON-RPC message per line on stdin, one
//! answer per request on stdout, a line that is not a message dropped,
//! a notification never answered, a request before `initialize`
//! refused as the SDK refuses it, an unknown method the spec's error.

use std::io::{self, BufRead, Write};

use serde_json::{Map, Value, json};

pub const INVALID_PARAMS: i64 = -32602;
pub const METHOD_NOT_FOUND: i64 = -32601;
const INVALID_PARAMS_TEXT: &str = "Invalid request parameters";
/// Reachable through the handshake (`HANDSHAKE_PROTOCOL_VERSIONS`); the
/// newest is the counter-offer to a stranger.
const HANDSHAKE_VERSIONS: [&str; 4] = ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"];

/// How a request is answered.
pub enum Answer {
    Result(Value),
    Error {
        code: i64,
        message: String,
        data: Option<Value>,
    },
}

impl Answer {
    pub fn invalid_params() -> Self {
        Self::Error {
            code: INVALID_PARAMS,
            message: INVALID_PARAMS_TEXT.to_string(),
            data: Some(json!("")),
        }
    }

    pub fn method_not_found(method: &str) -> Self {
        Self::Error {
            code: METHOD_NOT_FOUND,
            message: "Method not found".to_string(),
            data: Some(json!(method)),
        }
    }
}

/// The protocol version the handshake settles on: the client's when the
/// SDK speaks it, else the newest.
pub fn negotiate(params: Option<&Map<String, Value>>) -> &'static str {
    let requested = params
        .and_then(|params| params.get("protocolVersion"))
        .and_then(Value::as_str);
    HANDSHAKE_VERSIONS
        .iter()
        .copied()
        .find(|version| Some(*version) == requested)
        .unwrap_or(HANDSHAKE_VERSIONS[HANDSHAKE_VERSIONS.len() - 1])
}

/// What answers a method: `initialize` opens the session, the rest need
/// it open (`ping` excepted), and the handler sees the method's params.
pub trait Handler {
    fn handle(&mut self, method: &str, params: Option<&Map<String, Value>>) -> Answer;
}

/// Serve one session: read until EOF, answer every request in order.
pub fn serve(
    handler: &mut impl Handler,
    input: impl BufRead,
    mut out: impl Write,
) -> io::Result<()> {
    let mut initialized = false;
    for line in input.lines() {
        let line = line?;
        let Ok(Value::Object(message)) = serde_json::from_str::<Value>(&line) else {
            continue; // not a message: the SDK drops it without a word
        };
        let Some(method) = message.get("method").and_then(Value::as_str) else {
            continue; // a response or an error from the client
        };
        let id = message.get("id").filter(|id| !id.is_null());
        let params = message.get("params").and_then(Value::as_object);
        let Some(id) = id else {
            continue; // a notification; none changes a stdio session
        };
        let answer = if method == "initialize" {
            initialized = true;
            handler.handle(method, params)
        } else if !initialized && method != "ping" {
            Answer::invalid_params()
        } else {
            handler.handle(method, params)
        };
        let envelope = match answer {
            Answer::Result(result) => json!({"jsonrpc": "2.0", "id": id, "result": result}),
            Answer::Error {
                code,
                message,
                data,
            } => {
                let mut error = json!({"code": code, "message": message});
                if let Some(data) = data {
                    error["data"] = data;
                }
                json!({"jsonrpc": "2.0", "id": id, "error": error})
            }
        };
        writeln!(out, "{envelope}")?;
        out.flush()?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Echo;

    impl Handler for Echo {
        fn handle(&mut self, method: &str, params: Option<&Map<String, Value>>) -> Answer {
            match method {
                "initialize" => Answer::Result(json!({"protocolVersion": negotiate(params)})),
                "ping" => Answer::Result(json!({})),
                "tools/list" => Answer::Result(json!({"tools": []})),
                other => Answer::method_not_found(other),
            }
        }
    }

    fn session(lines: &str) -> Vec<Value> {
        let mut out = Vec::new();
        serve(&mut Echo, lines.as_bytes(), &mut out).unwrap();
        String::from_utf8(out)
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect()
    }

    #[test]
    fn the_session_opens_with_initialize_and_answers_in_order() {
        let answers = session(concat!(
            "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}\n",
            "{\"jsonrpc\":\"2.0\",\"id\":\"p\",\"method\":\"ping\"}\n",
            "not json\n",
            "[1, 2]\n",
            "{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}\n",
            "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\"}}\n",
            "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/list\",\"params\":{\"cursor\":\"x\"}}\n",
            "{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"resources/list\"}\n",
            "{\"jsonrpc\":\"2.0\",\"id\":5,\"result\":{}}\n",
        ));
        assert_eq!(answers.len(), 5);
        assert_eq!(
            answers[0],
            json!({"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "Invalid request parameters", "data": ""}})
        );
        assert_eq!(
            answers[1],
            json!({"jsonrpc": "2.0", "id": "p", "result": {}})
        );
        assert_eq!(answers[2]["result"]["protocolVersion"], "2024-11-05");
        assert_eq!(
            answers[3],
            json!({"jsonrpc": "2.0", "id": 3, "result": {"tools": []}})
        );
        assert_eq!(
            answers[4]["error"],
            json!({"code": -32601, "message": "Method not found", "data": "resources/list"})
        );
        let stranger = json!({"protocolVersion": "1999-01-01"});
        assert_eq!(negotiate(stranger.as_object()), "2025-11-25");
        assert_eq!(negotiate(None), "2025-11-25");
    }
}
