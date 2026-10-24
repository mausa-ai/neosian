use super::model::{Proposal, Request, parse};
use serde_json::json;

#[test]
fn operation_fields_cannot_smuggle_an_extra_mutation() {
    let base = json!({"format":1,"changes":[{"path":"/notes/a","operation":"delete",
        "reason":"empty","expected_version":1}]});
    let request: Request = parse(&base.to_string()).unwrap();
    request.validate().unwrap();
    for (key, value) in [
        ("body", json!("replacement")),
        ("restore_version", json!(1)),
        ("attributes", json!({})),
    ] {
        let mut changed = base.clone();
        changed["changes"][0][key] = value;
        let request: Request = parse(&changed.to_string()).unwrap();
        assert!(request.validate().is_err());
    }
    let mut forged = base;
    forged["approved"] = true.into();
    assert!(parse::<Request>(&forged.to_string()).is_err());
}

#[test]
fn proposal_digest_binds_content_and_source_but_not_object_order() {
    let value = json!({"format":1,"workspace":null,"observations":[{
        "path":"/notes/a","scope":"user:a","source":null,"version":1,"sha256":"0".repeat(64)}],
        "changes":[{"path":"/notes/a","operation":"delete","reason":"empty","content":null,"keeper":null,"restore_version":null}],"id":""});
    let mut proposal: Proposal = parse(&value.to_string()).unwrap();
    proposal.id = proposal.digest();
    proposal.validate().unwrap();
    let pretty = serde_json::to_string_pretty(&proposal).unwrap();
    parse::<Proposal>(&pretty).unwrap().validate().unwrap();
    proposal.observations[0].scope = "user:other".into();
    assert!(proposal.validate().is_err());
}
