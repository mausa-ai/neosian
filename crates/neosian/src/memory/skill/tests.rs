use super::*;

#[test]
fn a_skill_is_a_description_and_a_body() {
    let skill = skill_from_document(
        "deploy",
        "---\ndescription: Deploy to the eu-west-1 target.\n---\n\n# Deploy\n\nRun the deploy script.\n",
        "/project/skills/deploy",
    )
    .unwrap();
    assert_eq!(skill.description, "Deploy to the eu-west-1 target.");
    assert_eq!(skill.content, "# Deploy\n\nRun the deploy script.");
    let quoted = skill_from_document(
        "x",
        "---\nname: x\ndescription: \"Release: bump, test, tag.\" # when\nextra: 3\n---\nbody",
        "w",
    )
    .unwrap();
    assert_eq!(quoted.description, "Release: bump, test, tag.");
    assert_eq!(
        skill_from_document("x", "---\ndescription: 'it''s'\n---\nb", "w")
            .unwrap()
            .description,
        "it's"
    );
    let shipped = shipped_skills();
    assert_eq!(shipped.len(), 2);
    assert!(shipped[0].description.starts_with("Hand this session off"));
    assert!(shipped[0].content.starts_with("Write the handoff note now"));
}

#[test]
fn a_bad_skill_is_refused_with_the_python_sides_sentence() {
    let refused = |content: &str| skill_from_document("x", content, "/p/skills/x").unwrap_err();
    assert_eq!(
        refused("---\ndescription: Release this project: bump, test, tag.\n---\n# R\n"),
        "Invalid or missing frontmatter in skill file: /p/skills/x"
    );
    assert_eq!(
        refused("no frontmatter"),
        "Invalid or missing frontmatter in skill file: /p/skills/x"
    );
    assert_eq!(
        refused("---\ndescription: unclosed\n"),
        "Invalid or missing frontmatter in skill file: /p/skills/x"
    );
    assert_eq!(
        refused("---\nname: x\n---\nbody"),
        "Missing required key 'description' in skill frontmatter: /p/skills/x"
    );
    assert_eq!(
        refused("---\ndescription: 5\n---\nbody"),
        "Invalid or missing frontmatter in skill file: /p/skills/x — description must be a string"
    );
    assert_eq!(
        refused("---\ndescription: yes\n---\nbody"),
        "Invalid or missing frontmatter in skill file: /p/skills/x — description must be a string"
    );
    assert_eq!(
        refused("---\nname: y\ndescription: d\n---\nbody"),
        "Invalid or missing frontmatter in skill file: /p/skills/x — name 'y' does not match the address 'x'"
    );
    assert!(refused("---\n- item\n---\nbody").starts_with("Invalid or missing"));
    assert!(refused("---\ndescription: |\n  folded\n---\nbody").starts_with("Invalid or missing"));
}
