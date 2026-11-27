//! A server for the destination tests: each request is logged (its path
//! and JSON body) and answered by a closure with a status, extra headers
//! and a body; a handshake declares turn records version 1 unless the
//! closure answers it. The answers to `write_records` it builds are what
//! a store answers a probe and a one-part turn.

use std::io::{BufRead, BufReader, Read, Write as _};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};

use serde_json::{Value, json};

use crate::wire::WIRE_VERSION;

pub(crate) type Reply = (u16, Vec<(&'static str, String)>, Value);
type Answer = dyn Fn(&str, &Value) -> Option<Reply> + Send + Sync;

pub(crate) struct Server {
    pub(crate) url: String,
    pub(crate) log: Arc<Mutex<Vec<(String, Value)>>>,
    answer: Arc<Mutex<Box<Answer>>>,
    done: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

fn serve(mut stream: TcpStream, log: &Mutex<Vec<(String, Value)>>, answer: &Mutex<Box<Answer>>) {
    let mut reader = BufReader::new(&mut stream);
    let mut line = String::new();
    reader.read_line(&mut line).unwrap();
    let path = line.split(' ').nth(1).unwrap_or_default().to_string();
    let mut length = 0;
    loop {
        let mut header = String::new();
        reader.read_line(&mut header).unwrap();
        if header == "\r\n" || header.is_empty() {
            break;
        }
        if let Some((name, value)) = header.split_once(':')
            && name.eq_ignore_ascii_case("content-length")
        {
            length = value.trim().parse().unwrap();
        }
    }
    let mut body = vec![0; length];
    reader.read_exact(&mut body).unwrap();
    let body: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
    log.lock().unwrap().push((path.clone(), body.clone()));
    let declared = json!({"wire_version": WIRE_VERSION, "client": "client:test",
        "pageable": true, "erasable": false, "supports_optimistic_concurrency": false,
        "turn_records": {"version": 1}});
    let (status, headers, value) = match (answer.lock().unwrap())(&path, &body) {
        Some(reply) => reply,
        None if path == "/v1/capabilities" => (200, vec![], declared),
        None => (404, vec![], json!({})),
    };
    let text = value.to_string();
    let mut head = format!("HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n");
    for (name, value) in headers {
        head.push_str(&format!("{name}: {value}\r\n"));
    }
    head.push_str(&format!(
        "Content-Length: {}\r\nConnection: close\r\n\r\n",
        text.len()
    ));
    let _ = stream.write_all(format!("{head}{text}").as_bytes());
}

impl Server {
    pub(crate) fn new(
        answer: impl Fn(&str, &Value) -> Option<Reply> + Send + Sync + 'static,
    ) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let log = Arc::new(Mutex::new(Vec::new()));
        let answer: Arc<Mutex<Box<Answer>>> = Arc::new(Mutex::new(Box::new(answer)));
        let done = Arc::new(AtomicBool::new(false));
        let (kept, answers, finished) = (log.clone(), answer.clone(), done.clone());
        let thread = thread::spawn(move || {
            for stream in listener.incoming() {
                if finished.load(Ordering::SeqCst) {
                    return;
                }
                serve(stream.unwrap(), &kept, &answers);
            }
        });
        Self {
            url,
            log,
            answer,
            done,
            thread: Some(thread),
        }
    }

    /// Answers from now on with `answer`.
    pub(crate) fn answer(
        &self,
        answer: impl Fn(&str, &Value) -> Option<Reply> + Send + Sync + 'static,
    ) {
        *self.answer.lock().unwrap() = Box::new(answer);
    }

    /// The `write_records` bodies so far that carried records.
    pub(crate) fn parts(&self) -> Vec<Value> {
        let log = self.log.lock().unwrap();
        let parts = log.iter().filter(|(path, body)| {
            path.ends_with("write_records")
                && body["records"].as_array().is_some_and(|r| !r.is_empty())
        });
        parts.map(|(_, body)| body.clone()).collect()
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.done.store(true, Ordering::SeqCst);
        let _ = TcpStream::connect(self.url.trim_start_matches("http://"));
        let _ = self.thread.take().unwrap().join();
    }
}

/// A store's answer to `write_records` with `body`: a probe finds nothing,
/// a sealing part stores turn `turn`, any other part is taken.
pub(crate) fn written(body: &Value, turn: u64) -> Reply {
    let draft = body["draft"].clone();
    let records = body["records"].as_array().cloned().unwrap_or_default();
    let next = records
        .last()
        .map_or(0, |last| last["seq"].as_u64().unwrap() + 1);
    let bytes = body["turn_bytes"].as_u64().unwrap_or(1);
    let (state, header) = match (records.is_empty(), body["seal"] == true) {
        (true, _) => ("none", Value::Null),
        (false, true) => {
            let header = json!({"turn": turn, "created_at": "2026-11-27T00:00:00.000000Z",
                "actor": body["actor"], "redacted": false, "records": next, "bytes": bytes});
            ("sealed", header)
        }
        (false, false) => ("open", Value::Null),
    };
    let answer = json!({"draft": draft, "state": state, "next_seq": next, "turn": header,
        "stored_bytes": 0, "freed_bytes": 0});
    (200, vec![], answer)
}

/// A refusal with an envelope.
pub(crate) fn refusal(status: u16, code: &str, message: &str) -> Reply {
    let error = json!({"error": {"code": code, "message": message, "details": {}}});
    (status, vec![], error)
}
