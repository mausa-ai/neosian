use super::*;

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn oversized_legacy_row_serves_bounded_record_pages() {
    // A subprocess gives this case a private row budget without changing
    // the environment of other tests running in the same binary.
    if std::env::var(limits::RESPONSE).as_deref() != Ok("4096") {
        let result = std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                concat!(
                    module_path!(),
                    "::oversized_legacy_row_serves_bounded_record_pages"
                )
                .strip_prefix("neosian::")
                .unwrap(),
                "--ignored",
            ])
            .env(limits::RESPONSE, "4096")
            .status()
            .unwrap();
        assert!(result.success());
        return;
    }
    let db = Database::new();
    let large = "かな\n🐈\\\"".repeat(5_000);
    let small: Vec<String> = (0..9)
        .map(|i| format!("prompt {i}: {}", "abc".repeat(100)))
        .collect();
    let mut source: Vec<Value> = small
        .iter()
        .map(|t| json!({"role":"user","content":t}))
        .collect();
    source.push(json!({"role":"assistant","content":large}));
    db.store.append_turn(ID, messages(&source), None).unwrap();
    let refused = db.store.read_turns(ID, 0, None).err().unwrap().text();
    assert!(refused.contains(limits::RESPONSE), "{refused}");
    let mut read = RecordRead {
        heads: vec![(Kind::Reply, 28)],
        page_bytes: 1_200,
        ..Default::default()
    };
    let mut prompt_texts = Vec::new();
    let mut previews = 0;
    let mut pages = 0;
    loop {
        let position = db
            .store
            .read_records(ID, &read, &mut |item| {
                if let ReadItem::Record {
                    record,
                    tail: suffix,
                    truncated,
                    ..
                } = item
                {
                    if record.kind == Kind::Reply {
                        assert!(truncated);
                        assert_eq!(
                            record.text,
                            crate::conversation::turn_records::head(&large, 28)
                        );
                        assert_eq!(
                            suffix.as_deref(),
                            Some(crate::conversation::turn_records::tail(&large, 28))
                        );
                        previews += 1;
                    } else {
                        prompt_texts.push(record.text);
                    }
                }
                Ok(())
            })
            .unwrap();
        pages += 1;
        let Some(position) = position else { break };
        read.continuation = Some(position.encode(&read.fingerprint(ID)));
        assert!(pages <= small.len());
    }
    assert!(pages > 1);
    assert_eq!(prompt_texts, small);
    assert_eq!(previews, 1);
    let refused = db
        .store
        .read_records(
            ID,
            &RecordRead {
                kinds: Some(vec![Kind::Reply]),
                ..Default::default()
            },
            &mut |_| Ok(()),
        )
        .unwrap_err()
        .text();
    assert!(refused.contains(limits::RESPONSE), "{refused}");
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn mismatched_mapping_timestamp_is_refused_even_for_headers() {
    let db = Database::new();
    populate(&db.store);
    db.execute("UPDATE {{schema}}.record_turns SET created_at = created_at + interval '1 second' WHERE turn = 3");
    let refusal = db
        .store
        .read_records(
            ID,
            &RecordRead {
                turn: Some(3),
                kinds: Some(vec![]),
                ..Default::default()
            },
            &mut |_| panic!("invalid mapping must not reach its caller"),
        )
        .unwrap_err();
    assert!(refusal.text().contains("record header has invalid fields"));
}
