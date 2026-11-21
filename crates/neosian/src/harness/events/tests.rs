use serde_json::json;

use super::*;

#[test]
fn a_reader_gets_what_is_new_and_the_count_of_what_dropped() {
    let mut text = Text::default();
    text.push("one\n");
    assert_eq!(text.since(0, OUTPUT_BYTES), ("one\n".to_string(), 0));
    let mark = text.total();
    text.push("two\n");
    assert_eq!(text.since(mark, OUTPUT_BYTES), ("two\n".to_string(), 0));
    assert_eq!(text.since(text.total(), OUTPUT_BYTES), (String::new(), 0));
    assert_eq!(text.since(0, 4), ("two\n".to_string(), 4));
}

#[test]
fn the_middle_of_a_long_turn_is_counted_never_held() {
    let mut text = Text::default();
    let line = format!("{}é\n", "x".repeat(997));
    for _ in 0..400 {
        text.push(&line);
    }
    assert_eq!(text.total(), 400 * line.len());
    assert!(text.head.len() <= HEAD_BYTES && text.tail_bytes <= TAIL_BYTES);
    assert_eq!(
        text.tail_bytes,
        text.tail.iter().map(String::len).sum::<usize>()
    );
    let (shown, skipped) = text.since(0, OUTPUT_BYTES);
    assert!(shown.len() <= OUTPUT_BYTES && shown.ends_with("é\n"));
    assert_eq!(shown.len() + skipped, text.total());
    let (all, dropped) = text.since(0, usize::MAX);
    assert!(all.len() <= HEAD_BYTES + TAIL_BYTES && all.starts_with("xxx"));
    assert_eq!(all.len() + dropped, text.total());
    assert!(dropped > 0);
    let near_end = text.total() - line.len();
    assert_eq!(text.since(near_end, OUTPUT_BYTES), (line.clone(), 0));
    let big = "y".repeat(TAIL_BYTES * 2);
    text.push(&big);
    assert_eq!(text.tail_bytes, TAIL_BYTES);
    assert_eq!(text.since(text.total() - 3, 10), ("yyy".to_string(), 0));
}

#[test]
fn observed_entries_are_numbered_bounded_and_returned_newest() {
    let mut log = Log::default();
    for n in 0..300 {
        log.push(json!({"kind": "command", "n": n}));
    }
    assert_eq!(log.total(), 300);
    let (shown, skipped) = log.since(0, EVENTS_PER_ANSWER);
    assert_eq!(shown.len(), EVENTS_PER_ANSWER);
    assert_eq!(shown.last().unwrap()["n"], 299);
    assert_eq!(skipped, 300 - EVENTS_PER_ANSWER);
    assert_eq!(log.since(298, 10).0.len(), 2);
    assert_eq!(log.since(300, 10), (Vec::new(), 0));
    let Event::Observed(entry) = observed("tool", &[("name", json!("é".repeat(400)))]) else {
        panic!("an observed entry")
    };
    assert!(entry["name"].as_str().unwrap().len() <= FIELD_BYTES);
    assert_eq!(entry["kind"], "tool");
}

#[test]
fn cuts_fall_on_character_boundaries() {
    assert_eq!(head("aé", 2), "a");
    assert_eq!(tail("éa", 2), "a");
    let mut stderr = Tail::default();
    for _ in 0..5000 {
        stderr.push("é");
    }
    assert!(stderr.text().len() <= ERROR_BYTES && stderr.text().starts_with('é'));
}
