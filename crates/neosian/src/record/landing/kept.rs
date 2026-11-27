//! What a lander keeps of what a server said (ruling G3 of the T1e1
//! plan): a message stored in a held turn's reason, a destination's record
//! or the lander's summary has the client's own credential removed where
//! it appears, then is cut to its first 512 scalars. A gateway that echoes
//! the request's authorization header, or a record of the turn, leaves
//! neither in the home. A held turn's code is kept only when it is of the
//! error registry's grammar and holds no credential, else as `unknown`;
//! an import's error shows a server's message and code the same way
//! (ruling H6 of the T1e2 plan). The wire's own decoding stays verbatim
//! (`remote/records/failure.rs`); only what is kept or shown is cleaned.

use super::rules::Stop;
use super::state::Reason;
use crate::profile::files::Endpoint;
use crate::remote::selection::Selection;

/// The most of a message that is kept, in Unicode scalars.
pub const SCALARS: usize = 512;
/// What stands where a credential was.
pub const REMOVED: &str = "[credential]";
/// What stands for a code outside the error registry's grammar.
pub const UNKNOWN: &str = "unknown";

/// The client's own credentials as `selection` holds them: a token, a
/// DSN, a profile's source credentials, and the userinfo and query of a
/// URL or DSN, longest first so that none is left in part.
pub fn credentials(selection: &Selection) -> Vec<String> {
    let mut found = Vec::new();
    match selection {
        Selection::File(_) => {}
        Selection::Postgres { dsn, .. } => {
            found.push(dsn.clone());
            found.extend(parts(dsn));
        }
        Selection::Remote { url, token } => {
            found.push(token.clone());
            found.extend(parts(url));
        }
        Selection::Profile(resolved) => {
            for (source, binding) in &resolved.bindings {
                found.extend(resolved.credential(source).ok().flatten());
                if let Endpoint::Http { url, .. } = &binding.endpoint {
                    found.extend(parts(url));
                }
            }
        }
    }
    found.retain(|secret| !secret.is_empty());
    found.sort_by(|a, b| b.len().cmp(&a.len()).then_with(|| a.cmp(b)));
    found.dedup();
    found
}

/// The userinfo and the query of `url`, where it has them.
fn parts(url: &str) -> Vec<String> {
    let rest = url.split_once("://").map_or(url, |(_, rest)| rest);
    let authority = &rest[..rest.find(['/', '?', '#']).unwrap_or(rest.len())];
    let userinfo = authority.rsplit_once('@').map(|(userinfo, _)| userinfo);
    let query = rest
        .split_once('?')
        .map(|(_, query)| &query[..query.find('#').unwrap_or(query.len())]);
    [userinfo, query]
        .into_iter()
        .flatten()
        .map(str::to_string)
        .collect()
}

/// `message` as it is kept: every credential in `credentials` replaced by
/// `[credential]`, then its first 512 scalars.
pub fn kept(message: &str, credentials: &[String]) -> String {
    let mut cleaned = message.to_string();
    for secret in credentials {
        if cleaned.contains(secret.as_str()) {
            cleaned = cleaned.replace(secret.as_str(), REMOVED);
        }
    }
    cut(&cleaned)
}

/// The first 512 scalars of `message`.
pub fn cut(message: &str) -> String {
    message.chars().take(SCALARS).collect()
}

/// `stop` with its message as it is kept.
pub fn stop(stop: Stop, credentials: &[String]) -> Stop {
    let clean = |message: String| kept(&message, credentials);
    match stop {
        Stop::Conversation(message) => Stop::Conversation(clean(message)),
        Stop::Retry { seconds, message } => Stop::Retry {
            seconds,
            message: clean(message),
        },
        Stop::Wait {
            seconds,
            failing,
            message,
        } => Stop::Wait {
            seconds,
            failing,
            message: clean(message),
        },
        Stop::Block {
            block,
            message,
            status,
            code,
        } => Stop::Block {
            block,
            message: clean(message),
            status,
            code,
        },
    }
}

/// `reason` with its message and its code as they are kept.
pub fn reason(mut reason: Reason, credentials: &[String]) -> Reason {
    reason.message = reason.message.map(|message| kept(&message, credentials));
    if let Some(answered) = reason.answered.as_mut() {
        answered.code = answered.code.take().map(|said| code(&said, credentials));
    }
    reason
}

/// `code` as it is kept and shown: a code of the error registry's grammar
/// (a lowercase ASCII letter, then lowercase letters, digits and `_`, at
/// most 64 in all) that holds none of `credentials`, else `unknown`.
pub fn code(code: &str, credentials: &[String]) -> String {
    let grammar = code.len() <= 64
        && code.starts_with(|first: char| first.is_ascii_lowercase())
        && code
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_');
    let secret = credentials
        .iter()
        .any(|credential| code.contains(credential.as_str()));
    match grammar && !secret {
        true => code.to_string(),
        false => UNKNOWN.to_string(),
    }
}
