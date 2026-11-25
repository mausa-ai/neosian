use serde_json::{Value, json};

use super::super::{CHUNK, MEMORY, Writer};
use crate::pyfmt::dumps;

fn items(count: usize, each: usize) -> Vec<Value> {
    (0..count)
        .map(|n| json!({"n": n, "text": "é\n".repeat(each + n)}))
        .collect()
}

fn listed(writer: &mut Writer, items: &[Value]) {
    for (index, item) in items.iter().enumerate() {
        if index > 0 {
            writer.text(",").unwrap();
        }
        writer.value(item).unwrap();
    }
}

/// `{"turns":[first…],"records":[second…],"continuation":null}` with the
/// second list written into a spool of its own while the first is open.
fn answered(first: &[Value], second: &[Value], fail_after: Option<u64>) -> (Writer, String) {
    let mut writer = Writer::new();
    let mut records = writer.scratch();
    records.fail_after = fail_after;
    writer.text("{\"turns\":[").unwrap();
    // Interleaved, as a store hands a turn's header with its records.
    listed(&mut records, &second[..second.len() / 2]);
    listed(&mut writer, first);
    if second.len() > 1 {
        records.text(",").unwrap();
    }
    listed(&mut records, &second[second.len() / 2..]);
    writer.text("],\"records\":[").unwrap();
    writer.join(records).unwrap();
    writer.text("],\"continuation\":null}").unwrap();
    let whole = json!({"turns": first, "records": second, "continuation": null});
    (writer, dumps(&whole, false))
}

#[test]
fn a_joined_answer_is_the_whole_answer_with_its_length() {
    // Both parts in memory, the second on disk, and both on disk.
    for (first, second, disk) in [
        (items(3, 9), items(4, 9), [false, false]),
        (items(3, 9), items(300, 4000), [false, true]),
        (items(300, 4000), items(300, 4000), [true, true]),
        (items(0, 0), items(0, 0), [false, false]),
        (items(2, 5), items(1, 5), [false, false]),
    ] {
        let (writer, whole) = answered(&first, &second, None);
        assert_eq!(writer.on_disk(), disk);
        if disk[1] {
            assert!(whole.len() > 2 * MEMORY);
            // Nothing of a part on disk came back into memory to be joined.
            assert!(writer.in_memory_bytes() <= MEMORY as u64);
        }
        let (length, body) = writer.sent();
        assert_eq!(length, Some(whole.len().to_string()));
        assert_eq!(String::from_utf8(body).unwrap(), whole);
    }
}

#[test]
fn a_second_spool_failing_past_1_mib_finishes_in_memory_uncut() {
    let (first, second) = (items(3, 9), items(300, 4000));
    for after in [3 * MEMORY as u64 / 2, (2 * CHUNK + 17) as u64, 0] {
        let (writer, whole) = answered(&first, &second, Some(after));
        // The failed spool is read back whole; the first, written to while
        // the answer is past 1 MiB in memory, spills by itself.
        assert!(!writer.on_disk()[1], "read back whole");
        let (length, body) = writer.sent();
        assert_eq!(length, Some(whole.len().to_string()));
        assert_eq!(String::from_utf8(body).unwrap(), whole);
    }
}

#[test]
fn a_part_on_disk_keeps_its_own_file_until_the_answer_is_sent() {
    let (first, second) = (items(300, 4000), items(300, 4001));
    let (writer, whole) = answered(&first, &second, None);
    let directories: Vec<_> = writer
        .earlier
        .iter()
        .map(|part| match part {
            super::Part::File(_, scratch, _) => scratch.directory.clone(),
            super::Part::Memory(_) => panic!("both parts are on disk"),
        })
        .collect();
    assert_ne!(directories[0], directories[1]);
    assert!(directories.iter().all(|directory| directory.is_dir()));
    let (_, body) = writer.sent();
    assert_eq!(body.len(), whole.len());
    assert!(directories.iter().all(|directory| !directory.exists()));
}

fn refusal(error: std::io::Error) -> String {
    let refused = super::super::super::envelope::Fail::from(error);
    assert_eq!(
        (refused.status.as_u16(), refused.code),
        (400, "value_error")
    );
    refused.message
}

#[test]
fn two_spools_share_one_memory_allowance_and_spill_at_1_mib_together() {
    // 600,000 bytes each: neither passes 1 MiB alone, the two do, so the
    // one written to when the answer passes it goes to disk.
    let piece = "x".repeat(100_000);
    for second_last in [true, false] {
        let mut writer = Writer::new();
        let mut records = writer.scratch();
        for _ in 0..6 {
            if second_last {
                writer.text(&piece).unwrap();
            } else {
                records.text(&piece).unwrap();
            }
        }
        assert_eq!(writer.in_memory_bytes(), 600_000);
        assert!(writer.file.is_none() && records.file.is_none());
        for _ in 0..6 {
            if second_last {
                records.text(&piece).unwrap();
            } else {
                writer.text(&piece).unwrap();
            }
            assert!(writer.in_memory_bytes() <= MEMORY as u64);
        }
        assert_eq!(writer.in_memory_bytes(), 600_000);
        assert_eq!(
            (writer.file.is_some(), records.file.is_some()),
            (!second_last, second_last)
        );
        writer.join(records).unwrap();
        assert_eq!(writer.on_disk(), [!second_last, second_last]);
        writer.text("end").unwrap();
        let (length, body) = writer.sent();
        assert_eq!(length.as_deref(), Some("1200003"));
        assert!(body.len() == 1_200_003 && body.ends_with(b"xend"));
    }
}

#[test]
fn two_spools_are_refused_together_at_the_one_temporary_budget() {
    // 1.2 MB and 1.1 MB: each within a budget of 1.5 MB, the two not.
    use std::sync::atomic::Ordering;
    let piece = "x".repeat(100_000);
    let mut writer = Writer::new();
    writer.budget.temp.store(1_500_000, Ordering::Relaxed);
    let mut records = writer.scratch();
    for _ in 0..12 {
        writer.text(&piece).unwrap();
    }
    assert!(writer.file.is_some() && writer.in_memory_bytes() == 0);
    for _ in 0..10 {
        records.text(&piece).unwrap();
    }
    assert!(
        records.file.is_none(),
        "the second megabyte is still in memory"
    );
    assert_eq!(
        refusal(records.text(&piece).unwrap_err()),
        "resource limit NEOSIAN_MAX_TEMP_BYTES exceeded: 2200000 > 1500000; \
         select a smaller batch or raise this setting"
    );
    // Alone, either is within it.
    for pieces in [12, 11] {
        let mut alone = Writer::new();
        alone.budget.temp.store(1_500_000, Ordering::Relaxed);
        for _ in 0..pieces {
            alone.text(&piece).unwrap();
        }
        assert_eq!(alone.on_disk(), Vec::<bool>::new());
        assert!(alone.file.is_some());
    }
}

#[test]
fn parts_held_without_a_spool_stay_within_the_response_budget_together() {
    // No spool file can be written: each part alone fits the budget of
    // 100 bytes, the two together do not.
    let mut writer = Writer::new();
    let mut records = writer.scratch();
    (writer.memory, records.memory) = (Some(100), Some(100));
    writer.text(&"a".repeat(60)).unwrap();
    assert_eq!(
        refusal(records.text(&"b".repeat(60)).unwrap_err()),
        "resource limit NEOSIAN_MAX_RESPONSE_BYTES exceeded: 120 > 100; \
         select a smaller batch or raise this setting"
    );
    // Within it, what follows the join is still counted with the parts.
    let mut writer = Writer::new();
    let mut records = writer.scratch();
    records.memory = Some(100);
    writer.text(&"a".repeat(40)).unwrap();
    records.text(&"b".repeat(40)).unwrap();
    writer.join(records).unwrap();
    writer.text(&"c".repeat(20)).unwrap();
    assert!(writer.text("c").is_err());
    let (length, body) = writer.sent();
    assert_eq!((length.as_deref(), body.len()), (Some("100"), 100));
}
