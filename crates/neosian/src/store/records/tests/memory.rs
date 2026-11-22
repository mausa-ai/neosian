//! Memory during a write and a seal follows the part, not the turn (§40.54
//! item 16): a counting allocator, in test builds only, keeps the live and
//! peak bytes each thread allocated, and the bytes it was asked for in all,
//! and the peak of every part of a 16 MiB turn stays within that of a 1 MiB
//! turn's part, the seal's far below either. The full memory gate is T1g's;
//! this shows the shape.

use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;

use serde_json::json;

use super::{enabled, messages, part, parts, turns};
use crate::conversation::turn_records::records_of;

struct Counting;

thread_local! {
    static LIVE: Cell<isize> = const { Cell::new(0) };
    static PEAK: Cell<isize> = const { Cell::new(0) };
    static ASKED: Cell<usize> = const { Cell::new(0) };
}

fn add(delta: isize, asked: usize) {
    let _ = LIVE.try_with(|live| {
        let now = live.get() + delta;
        live.set(now);
        let _ = PEAK.try_with(|peak| peak.set(peak.get().max(now)));
    });
    let _ = ASKED.try_with(|total| total.set(total.get().saturating_add(asked)));
}

// SAFETY: every call goes to the system allocator unchanged; only the
// thread-local counters, which never allocate, are touched besides.
unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let pointer = unsafe { System.alloc(layout) };
        if !pointer.is_null() {
            add(layout.size() as isize, layout.size());
        }
        pointer
    }

    unsafe fn dealloc(&self, pointer: *mut u8, layout: Layout) {
        unsafe { System.dealloc(pointer, layout) };
        add(-(layout.size() as isize), 0);
    }

    unsafe fn realloc(&self, pointer: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        let moved = unsafe { System.realloc(pointer, layout, size) };
        if !moved.is_null() {
            add(size as isize - layout.size() as isize, size);
        }
        moved
    }
}

#[global_allocator]
static ALLOCATOR: Counting = Counting;

/// The most bytes `run` held at once beyond what was live before it.
pub(super) fn peak<T>(run: impl FnOnce() -> T) -> (T, usize) {
    let before = LIVE.with(Cell::get);
    PEAK.with(|peak| peak.set(before));
    let value = run();
    (value, (PEAK.with(Cell::get) - before).max(0) as usize)
}

/// The bytes `run` asked the allocator for in all, a reallocation counted
/// at its new size: a bound on the bytes it allocated and copied.
pub(super) fn asked<T>(run: impl FnOnce() -> T) -> (T, usize) {
    let before = ASKED.with(Cell::get);
    let value = run();
    (value, ASKED.with(Cell::get) - before)
}

/// The peak of each part and of the seal for a turn of one `size`-byte tool result.
fn peaks(name: &str, size: usize) -> (Vec<usize>, usize) {
    let (_home, store) = enabled(name);
    let result = format!("{}\n", "r".repeat(99)).repeat(size / 100);
    let turn = messages(&[
        json!({"role": "user", "content": "run it"}),
        json!({"role": "tool", "content": result, "tool_call_id": "t"}),
    ]);
    let records = records_of(&turn).unwrap();
    let mut seen = Vec::new();
    for chunk in parts(&records, 983_040) {
        let (answer, held) = peak(|| part(&store, "c-1", "d", chunk, false));
        answer.unwrap();
        seen.push(held);
    }
    let (sealed, seal) = peak(|| part(&store, "c-1", "d", Vec::new(), true));
    assert_eq!(
        sealed.unwrap().turn.unwrap().bytes,
        records.iter().map(|r| r.text.len() as u64).sum::<u64>()
    );
    assert_eq!(turns(&store, "c-1").len(), 1);
    (seen, seal)
}

#[test]
fn memory_during_a_write_and_a_seal_follows_the_part_not_the_turn() {
    let (small, small_seal) = peaks("records-memory-small", 1 << 20);
    let (large, large_seal) = peaks("records-memory-large", 16 << 20);
    assert!(large.len() >= 16, "{} parts", large.len());
    let typical = *small.iter().max().unwrap();
    let worst = *large.iter().max().unwrap();
    eprintln!(
        "peaks: 1 MiB turn parts {small:?}, seal {small_seal}; 16 MiB turn {} parts, most {worst}, seal {large_seal}",
        large.len()
    );
    assert!(
        worst <= typical + (256 << 10),
        "a 16 MiB turn's part held {worst} bytes, a 1 MiB turn's {typical}"
    );
    assert!(worst < 4 << 20, "a part of under 1 MiB held {worst} bytes");
    assert!(
        large_seal < 256 << 10 && small_seal < 256 << 10,
        "seals held {small_seal} and {large_seal} bytes"
    );
    assert!(
        large_seal <= small_seal + (16 << 10),
        "the seal does not grow with the turn: {small_seal} then {large_seal}"
    );
}
