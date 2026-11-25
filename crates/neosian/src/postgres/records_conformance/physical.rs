//! Physical row identity and mutations observed from the independent SQL client.
use super::Database;

impl Database {
    pub(super) fn record_addresses(&self) -> Vec<(i64, i32, i32, String, String)> {
        self.query(
            "SELECT owner, part, seq, xmin::text, ctid::text FROM {{schema}}.turn_records
             ORDER BY owner, part, seq",
        )
        .iter()
        .map(|r| (r.get(0), r.get(1), r.get(2), r.get(3), r.get(4)))
        .collect()
    }

    pub(super) fn observe_changes(&self) {
        self.execute(
            "CREATE TABLE {{schema}}.observed_changes (event text);
             CREATE FUNCTION {{schema}}.count_change() RETURNS trigger LANGUAGE plpgsql AS $$
             BEGIN
                 INSERT INTO {{schema}}.observed_changes VALUES (TG_TABLE_NAME || ' ' || TG_OP);
                 RETURN NULL;
             END $$",
        );
        for table in [
            "conversations",
            "turns",
            "projections",
            "turn_redactions",
            "record_drafts",
            "record_turns",
            "record_staging",
            "turn_records",
        ] {
            self.execute(&format!(
                "CREATE TRIGGER count_change AFTER INSERT OR UPDATE OR DELETE ON {{{{schema}}}}.{table}
                 FOR EACH ROW EXECUTE FUNCTION {{{{schema}}}}.count_change()"
            ));
        }
    }

    pub(super) fn changes(&self) -> Vec<String> {
        self.query("SELECT event FROM {{schema}}.observed_changes ORDER BY event")
            .iter()
            .map(|row| row.get(0))
            .collect()
    }
}
