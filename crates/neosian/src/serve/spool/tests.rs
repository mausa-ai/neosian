use super::*;
use serde_json::json;

fn collect(produce: Produce) -> (StatusCode, Option<String>, Vec<u8>) {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let response = respond(produce).await;
        let status = response.status();
        let length = response
            .headers()
            .get(CONTENT_LENGTH)
            .map(|value| value.to_str().unwrap().to_string());
        let body = axum::body::to_bytes(response.into_body(), usize::MAX).await;
        (status, length, body.map(|b| b.to_vec()).unwrap_or_default())
    })
}

fn answer(items: Vec<Value>) -> Produce {
    Box::new(move |writer| {
        writer.text("{\"turns\":[")?;
        for (index, item) in items.iter().enumerate() {
            if index > 0 {
                writer.text(",")?;
            }
            writer.value(item)?;
        }
        Ok(writer.text("],\"next_after\":null}")?)
    })
}

#[test]
fn a_spooled_answer_is_the_whole_answer() {
    for count in [3, 400] {
        let items: Vec<Value> = (0..count)
            .map(|i| json!({"n": i, "text": "é\n".repeat(i * 9)}))
            .collect();
        let whole = dumps(&json!({"turns": items, "next_after": null}), false);
        let (status, length, body) = collect(answer(items));
        assert_eq!(status, StatusCode::OK);
        if whole.len() > MEMORY {
            // From disk, with its length set explicitly.
            assert_eq!(length, Some(whole.len().to_string()));
        }
        assert_eq!(String::from_utf8(body).unwrap(), whole);
    }
}

/// Drive a writer directly: `between` runs once the answer has spilled.
fn written(
    items: &[Value],
    fail_after: Option<u64>,
    between: impl FnOnce(&Writer),
) -> (Option<String>, Vec<u8>) {
    let mut writer = Writer::new();
    writer.fail_after = fail_after;
    let mut between = Some(between);
    writer.text("{\"turns\":[").unwrap();
    for (index, item) in items.iter().enumerate() {
        if index > 0 {
            writer.text(",").unwrap();
        }
        writer.value(item).unwrap();
        if writer.file.is_some()
            && let Some(between) = between.take()
        {
            between(&writer);
        }
    }
    writer.text("],\"next_after\":null}").unwrap();
    let response = writer.response().unwrap();
    let length = response
        .headers()
        .get(CONTENT_LENGTH)
        .map(|value| value.to_str().unwrap().to_string());
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    let body = runtime
        .block_on(axum::body::to_bytes(response.into_body(), usize::MAX))
        .unwrap();
    (length, body.to_vec())
}

#[test]
fn a_spool_that_fails_midway_finishes_the_answer_in_memory() {
    // #419: a full disk (here, writes failing past 1.5 MiB on disk) or a
    // vanished directory never cuts the answer or turns it into a 500.
    let items: Vec<Value> = (0..300)
        .map(|i| json!({"n": i, "text": "é\n".repeat(4000 + i)}))
        .collect();
    let whole = dumps(&json!({"turns": items, "next_after": null}), false);
    assert!(whole.len() > 3 * MEMORY);
    let (length, body) = written(&items, Some(3 * MEMORY as u64 / 2), |_| {});
    assert_eq!(length, None, "answered from memory");
    assert_eq!(String::from_utf8(body).unwrap(), whole);
    // A directory removed under an open file (Unix; Windows refuses).
    #[cfg(unix)]
    {
        let (length, body) = written(&items, None, |writer| {
            let spilled = writer.file.as_ref().unwrap();
            std::fs::remove_dir_all(&spilled.scratch.directory).unwrap();
        });
        assert!(length.is_some());
        assert_eq!(String::from_utf8(body).unwrap(), whole);
    }
}

#[test]
fn a_large_encoded_item_uses_one_pending_chunk_and_recovers_its_suffix() {
    let items = [json!({"text": "é\nabcdef".repeat(MEMORY / 2)})];
    let whole = dumps(&json!({"turns": items, "next_after": null}), false);
    let (length, bytes) = written(&items, None, |writer| {
        assert_eq!(writer.file.as_ref().unwrap().pending.capacity(), CHUNK);
    });
    assert_eq!(length, Some(whole.len().to_string()));
    assert_eq!(bytes, whole.as_bytes());

    // A short write partway through this same item leaves some of its
    // input on disk, a partly written chunk, and several untouched chunks.
    let (length, bytes) = written(&items, Some((2 * CHUNK + 17) as u64), |_| {});
    assert_eq!(length, None);
    assert_eq!(bytes, whole.as_bytes());
}

#[test]
fn a_refusal_is_an_ordinary_response_wherever_it_is_found() {
    let (status, _, body) = collect(Box::new(|writer| {
        writer.text("{\"turns\":[")?;
        writer.encoded(format!("\"{}\"", "x".repeat(2 * MEMORY)))?;
        Err(Fail::value("found after two MiB"))
    }));
    assert_eq!(status, StatusCode::BAD_REQUEST);
    let refused: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(refused["error"]["message"], "found after two MiB");
}
