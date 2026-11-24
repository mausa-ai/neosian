//! Public record search under private candidate and response budgets.
use super::*;

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn relevance_counts_all_matching_records_across_formats_before_limiting() {
    if std::env::var(limits::SEARCH).as_deref() != Ok("3") {
        let test = std::thread::current();
        let ok = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", test.name().unwrap(), "--ignored"])
            .env(limits::SEARCH, "3")
            .status()
            .unwrap();
        assert!(ok.success());
        return;
    }
    let db = Database::new();
    restore(
        &db,
        "old",
        T0,
        &[json!({"role":"user","content":"cerulean"})],
    );
    write(
        &db,
        "new",
        "first",
        &[
            json!({"role":"user","content":"gold gold"}),
            json!({"role":"assistant","content":"cerulean gold"}),
        ],
        true,
    );
    let mut search = query("GOLD cerulean", Order::Relevance);
    let hits = db.store.search_records(&search).unwrap();
    // BM25 over three matches with lengths 8, 9, 13. Both terms have
    // document frequency two, so the mean length is 10 and idf is ln(1.6).
    assert_eq!(
        positions(&hits),
        [("new", 1, 1), ("new", 1, 0), ("old", 1, 0)]
    );
    assert_eq!(
        hits.iter().map(|h| h.score.unwrap()).collect::<Vec<_>>(),
        [837_253, 664_957, 511_885]
    );
    search.limit = 1;
    assert_eq!(db.store.search_records(&search).unwrap(), hits[..1]);
    write(
        &db,
        "new",
        "second",
        &[json!({"role":"user","content":"cerulean"})],
        true,
    );
    let refusal = db.store.search_records(&search).unwrap_err();
    assert_eq!(
        refusal.text(),
        limits::broad(3, "records", "the words, the kinds or the time range")
    );
    let newest = db
        .store
        .search_records(&query("gold cerulean", Order::Newest))
        .unwrap();
    assert_eq!(positions(&newest), [("new", 1, 1)]);
    search.conversations = Some(vec!["old".into()]);
    assert_eq!(
        positions(&db.store.search_records(&search).unwrap()),
        [("old", 1, 0)]
    );
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn wide_legacy_json_produces_a_bounded_record_hit() {
    if std::env::var(limits::RESPONSE).as_deref() != Ok("4096") {
        let test = std::thread::current();
        let ok = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", test.name().unwrap(), "--ignored"])
            .env(limits::RESPONSE, "4096")
            .status()
            .unwrap();
        assert!(ok.success());
        return;
    }
    let db = Database::new();
    let mut data: Vec<_> = (0..64)
        .map(|i| json!({"role":"user","content":format!("message {i} {}","x".repeat(1024))}))
        .collect();
    let needle = format!("{}anchor", "界".repeat(100));
    data.push(json!({"role":"assistant","content":needle}));
    restore(&db, "archive", T0, &data);
    assert!(
        db.store
            .read_turns("archive", 0, None)
            .unwrap_err()
            .text()
            .contains(limits::RESPONSE)
    );
    for order in [Order::Newest, Order::Relevance] {
        let hits = db.store.search_records(&query("anchor", order)).unwrap();
        assert_eq!(positions(&hits), [("archive", 1, 64)]);
        assert_eq!(hits[0].excerpt, (60, format!("{}anchor", "界".repeat(40))));
        assert_eq!((hits[0].message, hits[0].size), (64, 306));
    }
}
