//! The draft check against `messages_of` (§40.54 item 9 "Checks"): it
//! accepts every converted turn, part by part, its state carried through
//! JSON between records as a store keeps it; and over mutated record sets
//! it never accepts what `messages_of` refuses, and refuses what it accepts
//! only where a JSON piece's text was changed, since it reads compact JSON
//! alone.

use serde_json::Value;

use super::generate::{self, Rng};
use super::message;
use crate::conversation::message::Role;
use crate::conversation::turn_records::{
    DraftCheck, Form, Kind, Refusal, Shape, TurnRecord, messages_of, records_of,
};

const MAX: u64 = 1 << 30;

/// Every record through a check whose state is written out and read back
/// before each one; whether the draft could then be sealed.
fn checked(records: &[TurnRecord]) -> Result<DraftCheck, String> {
    let mut check = DraftCheck::default();
    for record in records {
        let kept: Value = serde_json::to_value(&check).unwrap();
        check = serde_json::from_value(kept).unwrap();
        check
            .admit(record, None, MAX)
            .map_err(|refusal| format!("{refusal:?}"))?;
    }
    check.sealable()?;
    Ok(check)
}

fn turn(rng: &mut Rng, big: bool) -> Vec<TurnRecord> {
    let messages: Vec<_> = (0..=rng.below(4))
        .map(|_| message(&generate::message(rng, big)))
        .collect();
    records_of(&messages).unwrap()
}

#[test]
fn every_converted_turn_passes_the_check_part_by_part() {
    for seed in 0..1_000 {
        let mut rng = Rng::new(30_000 + seed);
        let records = turn(&mut rng, seed % 50 == 0);
        let check = checked(&records).unwrap_or_else(|why| panic!("seed {seed}: {why}"));
        assert_eq!(check.next_seq(), records.len() as u64);
        let bytes: u64 = records.iter().map(|record| record.text.len() as u64).sum();
        assert_eq!(check.bytes(), bytes, "the sealed turn's bytes");
    }
}

/// One change to one record, and whether it touched a JSON piece's text.
fn mutate(records: &mut [TurnRecord], rng: &mut Rng) -> bool {
    let at = rng.below(records.len() as u64) as usize;
    let record = &mut records[at];
    let json = Shape::of(record.kind).is_some();
    match rng.below(11) {
        0 => record.seq += 1 + rng.below(2),
        1 => record.chunk = rng.below(3),
        2 => record.message = rng.below(3),
        3 => {
            record.content = [None, Some(Form::Text), Some(Form::Blocks), Some(Form::None)]
                [rng.below(4) as usize]
        }
        4 => record.kind = Kind::ALL[rng.below(8) as usize],
        5 => record.block = [None, Some(0), Some(1)][rng.below(3) as usize],
        6 => record.call = [None, Some(0), Some(1)][rng.below(3) as usize],
        7 => {
            record.call_id =
                [None, Some("c".to_string()), Some(String::new())][rng.below(3) as usize].clone()
        }
        8 => record.size = rng.below(record.size + 3),
        9 => {
            record.role =
                [Role::User, Role::Assistant, Role::System, Role::Tool][rng.below(4) as usize]
        }
        _ => {
            let cut = rng.below(record.text.len() as u64 + 1) as usize;
            let cut = (cut..=record.text.len())
                .find(|at| record.text.is_char_boundary(*at))
                .unwrap();
            record.text = format!("{}{}", &record.text[..cut], generate::text(rng, 3));
            record.size = record.text.len() as u64;
            return json;
        }
    }
    false
}

#[test]
fn the_check_agrees_with_messages_of_over_mutated_record_sets() {
    let (mut refused, mut stricter) = (0, 0);
    for seed in 0..4_000 {
        let mut rng = Rng::new(40_000 + seed);
        let mut records = turn(&mut rng, false);
        let touched_json = mutate(&mut records, &mut rng);
        let rebuilt = messages_of(&records);
        let check = checked(&records);
        if check.is_ok() {
            assert!(
                rebuilt.is_ok(),
                "seed {seed}: the check accepted what messages_of refuses: {:?}",
                rebuilt.err()
            );
        } else if rebuilt.is_ok() {
            assert!(
                touched_json,
                "seed {seed}: refused {:?}, which messages_of accepts",
                check.err()
            );
            stricter += 1;
        } else {
            refused += 1;
        }
    }
    assert!(refused > 1_000, "mutations reached refusals: {refused}");
    assert!(stricter < 400, "stricter only on JSON text: {stricter}");
}

#[test]
fn a_stated_total_and_the_bound_refuse_by_name() {
    let records = records_of(&[message(
        &serde_json::json!({"role": "user", "content": "x".repeat(100)}),
    )])
    .unwrap();
    let mut check = DraftCheck::default();
    assert_eq!(
        check.admit(&records[0], Some(99), MAX),
        Err(Refusal::Stated {
            stated: 99,
            bytes: 100
        })
    );
    assert_eq!(
        check,
        DraftCheck::default(),
        "nothing kept: a store clones before a request"
    );
    let mut check = DraftCheck::default();
    assert_eq!(
        check.admit(&records[0], None, 99),
        Err(Refusal::Bound {
            bytes: 100,
            maximum: 99
        })
    );
    let mut check = DraftCheck::default();
    assert_eq!(check.admit(&records[0], Some(100), 100), Ok(()));
    assert_eq!(check.sealable(), Ok(()));
    assert!(
        DraftCheck::default()
            .sealable()
            .unwrap_err()
            .contains("at least one record")
    );
}

#[test]
fn a_draft_ending_inside_a_piece_or_a_message_cannot_be_sealed() {
    let long = message(
        &serde_json::json!({"role": "tool", "content": "y".repeat(140_000), "tool_call_id": "t"}),
    );
    let records = records_of(&[long]).unwrap();
    assert_eq!(records.len(), 3);
    let mut check = DraftCheck::default();
    check.admit(&records[0], None, MAX).unwrap();
    assert!(
        check
            .sealable()
            .unwrap_err()
            .contains("ends inside a piece")
    );
    let mut wrong = records[1].clone();
    wrong.chunk = 2;
    assert!(matches!(
        check.clone().admit(&wrong, None, MAX),
        Err(Refusal::Invalid(_))
    ));
    check.admit(&records[1], None, MAX).unwrap();
    check.admit(&records[2], None, MAX).unwrap();
    assert_eq!(check.sealable(), Ok(()));
}
