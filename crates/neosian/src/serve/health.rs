//! The distroless image's probe: the same executable calls the open
//! `/health` route, without credentials, a proxy, a store or a runtime.

use std::io::{self, Write};
use std::time::Duration;

use serde_json::{Value, json};

fn healthy(url: &str, timeout: Duration) -> Result<bool, ureq::Error> {
    let agent = ureq::Agent::config_builder()
        .proxy(None)
        .max_redirects(0)
        .http_status_as_error(false)
        .timeout_global(Some(timeout))
        .build()
        .new_agent();
    let mut response = agent.get(url).call()?;
    Ok(response.status() == 200
        && response
            .body_mut()
            .with_config()
            .limit(1024)
            .read_json::<Value>()?
            == json!({"status": "ok"}))
}

pub fn run(host: &str, port: u16, err: &mut impl Write) -> io::Result<u8> {
    let host = if host.contains(':') && !host.starts_with('[') {
        format!("[{host}]")
    } else {
        host.to_string()
    };
    match healthy(
        &format!("http://{host}:{port}/health"),
        Duration::from_secs(2),
    ) {
        Ok(true) => Ok(0),
        Ok(false) => {
            writeln!(err, "healthcheck: /health did not report ok")?;
            Ok(1)
        }
        Err(error) => {
            writeln!(err, "healthcheck: {error}")?;
            Ok(1)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader};
    use std::net::TcpListener;
    use std::thread;

    fn probe(status: u16, body: &str, delay: Duration) -> Result<bool, ureq::Error> {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}/health", listener.local_addr().unwrap());
        let body = body.to_string();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            let mut reader = BufReader::new(&mut stream);
            let mut request = String::new();
            reader.read_line(&mut request).unwrap();
            assert_eq!(request, "GET /health HTTP/1.1\r\n");
            loop {
                let mut line = String::new();
                assert_ne!(reader.read_line(&mut line).unwrap(), 0);
                assert!(!line.to_ascii_lowercase().starts_with("authorization:"));
                if line == "\r\n" {
                    break;
                }
            }
            thread::sleep(delay);
            let _ = write!(
                stream,
                "HTTP/1.1 {status} Response\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
        });
        let timeout = if delay.is_zero() {
            Duration::from_secs(2)
        } else {
            Duration::from_millis(100)
        };
        let result = healthy(&url, timeout);
        server.join().unwrap();
        result
    }

    #[test]
    fn health_requires_the_open_routes_status_and_body() {
        assert!(probe(200, r#"{"status":"ok"}"#, Duration::ZERO).unwrap());
        for (status, body) in [(503, r#"{"status":"ok"}"#), (200, r#"{"status":"bad"}"#)] {
            assert!(!probe(status, body, Duration::ZERO).unwrap());
        }
        assert!(probe(200, "not JSON", Duration::ZERO).is_err());
        assert!(probe(200, &"x".repeat(1025), Duration::ZERO).is_err());
    }

    #[test]
    fn an_unresponsive_server_times_out() {
        assert!(matches!(
            probe(200, r#"{"status":"ok"}"#, Duration::from_millis(300)),
            Err(ureq::Error::Timeout(_))
        ));
    }
}
