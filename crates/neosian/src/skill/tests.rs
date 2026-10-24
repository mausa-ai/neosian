use super::model::{Spec, address};
use crate::knowledge::model::{Content, Metadata};
use crate::memory::skill::skill_from_document;
use serde_json::json;

#[test]
fn managed_headers_keep_legacy_skill_names_and_frontmatter_meaning() {
    assert!(address("/handoff/skills/handoff").is_ok());
    let mut content = Content {
        metadata: Metadata::empty("skills"),
        body:
            "---\r\nname: handoff\r\ndescription: Save the next step\r\n---\r\nContinue here.\r\n"
                .into(),
    };
    content
        .metadata
        .attributes
        .insert("skill".into(), json!({"format":1}));
    let skill = skill_from_document("handoff", &content.render().unwrap(), "source").unwrap();
    assert_eq!(skill.description, "Save the next step");
    assert_eq!(skill.content, "Continue here.");
    content
        .metadata
        .attributes
        .insert("skill".into(), json!({"format":9}));
    assert!(skill_from_document("handoff", &content.render().unwrap(), "source").is_err());
}

#[test]
fn text_paths_cannot_alias_directories_or_control_files() {
    for names in [["a", "a/b"], ["a/x", "A/y"], ["SKILL.md/x", "ok"]] {
        let mut spec = Spec::default();
        for name in names {
            spec.files.insert(name.into(), "0".repeat(64));
        }
        assert!(spec.validate().is_err());
    }
    let mut spec = Spec::default();
    spec.files
        .insert("messages/example.md".into(), "0".repeat(64));
    assert!(spec.validate().is_ok());
}
