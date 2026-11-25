use super::Database;

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn disabled_sweeps_report_the_missing_extension_before_reading_record_settings() {
    // A child process gives this test its own settings while other stores run.
    if std::env::var("NEOSIAN_DRAFT_SECONDS").as_deref() != Ok("59") {
        let result = std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                std::thread::current().name().unwrap(),
                "--ignored",
            ])
            .env("NEOSIAN_DRAFT_SECONDS", "59")
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stdout)
        );
        return;
    }
    let db = Database::new();
    for enabled in [true, false] {
        if !enabled {
            db.execute("DROP TABLE {{schema}}.record_staging, {{schema}}.record_turns, {{schema}}.turn_records, {{schema}}.record_drafts, {{schema}}.neosian_records");
        }
        let store = db.another_store();
        for write in [false, true] {
            let error = store.sweep_records(1, None, write).unwrap_err();
            let expected = if enabled {
                "NEOSIAN_DRAFT_SECONDS must be an integer in 60..2592000"
            } else {
                "records are not enabled on this store; install Postgres records extension 1"
            };
            assert!(error.text().contains(expected), "{}", error.text());
        }
    }
}
