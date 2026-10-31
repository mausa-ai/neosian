//! Optional host observation, independent of storage, hooks and model execution.

use std::io::{self, Read, Write};
use std::time::{SystemTime, UNIX_EPOCH};

use clap::{Args, ValueEnum};
use serde::Serialize;
use serde_json::Value;
use std::path::PathBuf;

const MAX_INPUT: u64 = 1024 * 1024;

#[derive(Clone, Copy, ValueEnum)]
enum Client {
    ClaudeCode,
}

#[derive(Args)]
pub struct Statusline {
    /// Client supplying the documented statusline payload.
    #[arg(long, value_enum)]
    client: Client,
    /// Print only normalized cache fields as JSON.
    #[arg(long)]
    json: bool,
    /// Supply the installed Claude mod with sanitized observations and preserve the prior statusline.
    #[arg(long, conflicts_with = "json")]
    bridge: Option<PathBuf>,
}

#[derive(Debug, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
enum State {
    Unknown,
    Warm,
    Cold,
}

#[derive(Debug, Serialize)]
struct Cache {
    state: State,
    ttl: Option<&'static str>,
    expires_at: Option<u64>,
    remaining_seconds: Option<u64>,
    recache_tokens_if_cold: Option<u64>,
    last_main_cache_read_tokens: Option<u64>,
    last_main_cache_write_tokens: Option<u64>,
    main_requests: Option<u64>,
    main_misses: Option<u64>,
}

impl Cache {
    fn from_payload(payload: &Value, now: u64) -> Self {
        let cache = &payload["prompt_cache"];
        let usage = &payload["context_window"]["current_usage"];
        let ttl = match cache["ttl"].as_str() {
            Some("5m") => Some("5m"),
            Some("1h") => Some("1h"),
            _ => None,
        };
        let expires_at = cache["expires_at"].as_u64();
        let state = match (cache["caching_observed"].as_bool(), cache["warm"].as_bool()) {
            (Some(false), _) | (_, None) => State::Unknown,
            (_, Some(false)) => State::Cold,
            (_, Some(true)) if expires_at.is_some_and(|expiry| expiry <= now) => State::Cold,
            (_, Some(true)) => State::Warm,
        };
        let remaining_seconds = (state == State::Warm)
            .then(|| expires_at.map(|expiry| expiry.saturating_sub(now)))
            .flatten();
        Self {
            state,
            ttl,
            expires_at,
            remaining_seconds,
            recache_tokens_if_cold: cache["recache_tokens_if_cold"].as_u64(),
            last_main_cache_read_tokens: usage["cache_read_input_tokens"].as_u64(),
            last_main_cache_write_tokens: usage["cache_creation_input_tokens"].as_u64(),
            main_requests: cache["requests"].as_u64(),
            main_misses: cache["misses"].as_u64(),
        }
    }

    fn display(&self) -> String {
        let mut text = match self.state {
            State::Unknown => "cache unknown".to_owned(),
            State::Cold => "cache estimate cold".to_owned(),
            State::Warm => match self.remaining_seconds {
                Some(seconds) => {
                    format!("cache estimate warm ~{}m{:02}s", seconds / 60, seconds % 60)
                }
                None => "cache estimate warm (expiry unknown)".to_owned(),
            },
        };
        if let (Some(read), Some(written)) = (
            self.last_main_cache_read_tokens,
            self.last_main_cache_write_tokens,
        ) {
            text.push_str(&format!(" | last main read {read}, wrote {written}"));
        }
        if let Some(misses) = self.main_misses {
            text.push_str(&format!(" | misses {misses}"));
        }
        if let Some(tokens) = self.recache_tokens_if_cold {
            text.push_str(&format!(" | cold rebuild ~{tokens} tokens"));
        }
        text
    }
}

pub fn run(args: &Statusline, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let mut input = Vec::new();
    // Parse bounded host input before any optional observation write. This path
    // never opens a store/profile or makes a network or model request.
    io::stdin()
        .lock()
        .take(MAX_INPUT + 1)
        .read_to_end(&mut input)?;
    let payload = (input.len() as u64 <= MAX_INPUT)
        .then(|| serde_json::from_slice::<Value>(&input).ok())
        .flatten()
        .filter(Value::is_object);
    let Some(payload) = payload else {
        // Never echo host input (it can contain credentials, paths or terminal escapes).
        writeln!(err, "statusline: expected a JSON object of at most 1 MiB")?;
        return Ok(1);
    };
    let elapsed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let now = elapsed.as_secs();
    let cache = Cache::from_payload(&payload, now);
    if let Some(directory) = &args.bridge {
        return super::cache_bridge::run(
            directory,
            &payload,
            &serde_json::to_value(&cache)?,
            &input,
            elapsed.as_millis().try_into().unwrap_or(u64::MAX),
            out,
            err,
        );
    }
    if args.json {
        writeln!(out, "{}", serde_json::to_string(&cache)?)?;
    } else {
        writeln!(out, "{}", cache.display())?;
    }
    Ok(0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn expiry_is_checked_against_the_clock() {
        let payload = json!({"prompt_cache": {
            "warm": true, "ttl": "5m", "expires_at": 1300,
            "recache_tokens_if_cold": 18285
        }});
        assert_eq!(
            Cache::from_payload(&payload, 1001).display(),
            "cache estimate warm ~4m59s | cold rebuild ~18285 tokens"
        );
        let expired = Cache::from_payload(&payload, 1300);
        assert_eq!(expired.state, State::Cold);
        assert_eq!(expired.remaining_seconds, None);
    }

    #[test]
    fn unobserved_is_not_a_cache_miss() {
        let payload = json!({"prompt_cache": {"warm": false, "caching_observed": false}});
        assert_eq!(
            Cache::from_payload(&payload, 1000).display(),
            "cache unknown"
        );
    }
}
