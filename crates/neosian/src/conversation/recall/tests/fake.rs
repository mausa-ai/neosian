//! Stores made up for recall's tests: one answering one record per page,
//! each header repeated, keeping every request it is asked; one refusing.

use std::cell::RefCell;

use super::super::origin::{Item, Origin};
use crate::conversation::turn_records::sweep::SweepPage;
use crate::conversation::turn_records::{
    Header, RecordHit, RecordRead, Restore, Restored, TurnRecord, Write, Written,
};
use crate::conversation::{Error, Refused};
use crate::remote::records::{RecordPage, RecordQuery};
use crate::state::records::{Fault, Records};
use crate::store::time::Stamp;

pub struct Paged {
    pub header: Header,
    pub records: Vec<TurnRecord>,
    pub asked: RefCell<Vec<RecordRead>>,
}

impl Paged {
    pub fn new(records: Vec<TurnRecord>) -> Self {
        let stamp = Stamp::parse("2026-11-27T10:00:00Z").unwrap();
        Self {
            header: Header::of(1, stamp, None, &records),
            records,
            asked: RefCell::new(Vec::new()),
        }
    }
}

fn not_here<T>() -> Result<T, Fault> {
    Err(Fault::Local(Error::Value("not asked here".into())))
}

impl Records for Paged {
    fn write(&self, _: &str, _: Write) -> Result<Written, Fault> {
        not_here()
    }

    fn read(
        &self,
        _: &str,
        read: &RecordRead,
        visit: &mut dyn FnMut(RecordPage) -> Result<bool, Fault>,
    ) -> Result<(), Fault> {
        self.asked.borrow_mut().push(read.clone());
        let origin = Origin::Whole {
            header: self.header.clone(),
            records: self.records.clone(),
        };
        let (mut turns, mut answered) = (Vec::new(), Vec::new());
        origin.read(read, &mut |item| {
            match item {
                Item::Header(header) => turns.push(header),
                Item::Record(record) => answered.push(record),
            }
            Ok(true)
        })?;
        let count = answered.len();
        if count == 0 {
            let page = RecordPage {
                turns,
                records: Vec::new(),
                continuation: None,
            };
            return visit(page).map(drop);
        }
        for (at, record) in answered.into_iter().enumerate() {
            let page = RecordPage {
                turns: turns.clone(),
                records: vec![record],
                continuation: (at + 1 < count).then(|| "next".to_string()),
            };
            if !visit(page)? {
                break;
            }
        }
        Ok(())
    }

    fn search(&self, _: &RecordQuery) -> Result<Vec<RecordHit>, Fault> {
        not_here()
    }

    fn restore(&self, _: &str, _: Restore) -> Result<Restored, Fault> {
        not_here()
    }

    fn sweep(&self, _: usize, _: Option<&str>, _: bool) -> Result<SweepPage, Fault> {
        not_here()
    }
}

/// A store that keeps records and refuses every read.
pub struct Refuses {
    code: &'static str,
    message: String,
}

impl Refuses {
    pub fn new(code: &'static str, message: &str) -> Self {
        let message = message.to_string();
        Self { code, message }
    }
}

impl Records for Refuses {
    fn write(&self, _: &str, _: Write) -> Result<Written, Fault> {
        not_here()
    }

    fn read(
        &self,
        _: &str,
        _: &RecordRead,
        _: &mut dyn FnMut(RecordPage) -> Result<bool, Fault>,
    ) -> Result<(), Fault> {
        let (code, message) = (self.code, self.message.clone());
        let details = serde_json::Value::Null;
        let refused = Refused {
            code,
            message,
            details,
        };
        Err(Fault::Local(Error::Conversation(refused)))
    }

    fn search(&self, _: &RecordQuery) -> Result<Vec<RecordHit>, Fault> {
        not_here()
    }

    fn restore(&self, _: &str, _: Restore) -> Result<Restored, Fault> {
        not_here()
    }

    fn sweep(&self, _: usize, _: Option<&str>, _: bool) -> Result<SweepPage, Fault> {
        not_here()
    }
}
