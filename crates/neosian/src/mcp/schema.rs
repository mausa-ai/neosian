//! A tool's arguments as the schema promises them (`tools/schema.py`,
//! DESIGN §27.9): the JSON Schema the client lists, and the validation
//! every call passes, worded as pydantic words it so the corrective
//! failure is the same text on every transport. Bounded to the shapes the
//! declared tools use: strings, integers, booleans, integer/string lists
//! and literal sets; optional, defaulted or required.

use serde_json::{Map, Value, json};

#[derive(Clone, Copy)]
pub enum Kind {
    Str,
    Int,
    StrictInt,
    Bool,
    IntList,
    StrList,
    Literal(&'static [&'static str]),
}

/// One parameter: required, optional (null allowed, the default null),
/// or defaulted (a value when absent).
#[derive(Clone, Copy)]
pub enum Presence {
    Required,
    Optional,
    Default(&'static str),
}

pub struct Field {
    pub name: &'static str,
    pub kind: Kind,
    pub presence: Presence,
}

/// The `inputSchema` of a tool whose parameters are `fields`, their prose
/// from `described` by name.
pub fn input_schema(fields: &[Field], described: &[(String, String)]) -> Value {
    let mut properties = Map::new();
    let mut required = Vec::new();
    for field in fields {
        let description = described
            .iter()
            .find(|(name, _)| name == field.name)
            .map(|(_, prose)| prose.clone())
            .unwrap_or_default();
        let typed = match field.kind {
            Kind::Str => json!({"type": "string"}),
            Kind::Int | Kind::StrictInt => json!({"type": "integer"}),
            Kind::Bool => json!({"type": "boolean"}),
            Kind::IntList => json!({"items": {"type": "integer"}, "type": "array"}),
            Kind::StrList => json!({"items": {"type": "string"}, "type": "array"}),
            Kind::Literal(choices) => json!({"enum": choices, "type": "string"}),
        };
        let mut property = Map::new();
        match field.presence {
            Presence::Required => {
                required.push(field.name);
                property.insert("description".to_string(), json!(description));
                merge(&mut property, typed);
            }
            Presence::Optional => {
                property.insert("anyOf".to_string(), json!([typed, {"type": "null"}]));
                property.insert("default".to_string(), Value::Null);
                property.insert("description".to_string(), json!(description));
            }
            Presence::Default(text) => {
                property.insert("default".to_string(), default_value(field.kind, text));
                property.insert("description".to_string(), json!(description));
                merge(&mut property, typed);
            }
        }
        properties.insert(field.name.to_string(), Value::Object(property));
    }
    let mut schema = json!({"properties": properties});
    if !required.is_empty() {
        schema["required"] = json!(required);
    }
    schema["type"] = json!("object");
    schema["additionalProperties"] = json!(false);
    schema
}

fn merge(property: &mut Map<String, Value>, typed: Value) {
    if let Value::Object(typed) = typed {
        for (key, value) in typed {
            property.insert(key, value);
        }
    }
}

fn default_value(kind: Kind, text: &str) -> Value {
    match kind {
        Kind::Int | Kind::StrictInt => json!(text.parse::<i64>().unwrap_or_default()),
        Kind::StrList => serde_json::from_str(text).expect("string-list default"),
        Kind::Bool => json!(text == "true"),
        _ => json!(text),
    }
}

fn join_choices(choices: &[&str]) -> String {
    let quoted: Vec<String> = choices.iter().map(|choice| format!("'{choice}'")).collect();
    match quoted.len() {
        0 => String::new(),
        1 => quoted[0].clone(),
        n => format!("{} or {}", quoted[..n - 1].join(", "), quoted[n - 1]),
    }
}

/// One value as pydantic's lax mode reads it, or its message.
fn coerce(kind: Kind, value: &Value) -> Result<Value, String> {
    match kind {
        Kind::Str => match value {
            Value::String(_) => Ok(value.clone()),
            _ => Err("Input should be a valid string".to_string()),
        },
        Kind::Int => match value {
            Value::Number(number) if number.is_i64() || number.is_u64() => Ok(value.clone()),
            Value::Number(number) => match number.as_f64() {
                Some(float) if float.fract() == 0.0 => Ok(json!(float as i64)),
                _ => Err(
                    "Input should be a valid integer, got a number with a fractional part"
                        .to_string(),
                ),
            },
            Value::Bool(flag) => Ok(json!(i64::from(*flag))),
            Value::String(text) => match text.trim().parse::<i64>() {
                Ok(parsed) => Ok(json!(parsed)),
                Err(_) => Err(
                    "Input should be a valid integer, unable to parse string as an integer"
                        .to_string(),
                ),
            },
            _ => Err("Input should be a valid integer".to_string()),
        },
        Kind::StrictInt => match value {
            Value::Number(n) if n.is_i64() || n.is_u64() => Ok(value.clone()),
            _ => Err("Input should be a valid integer".into()),
        },
        Kind::Bool => match value {
            Value::Bool(_) => Ok(value.clone()),
            Value::Number(number) if number.as_i64() == Some(0) => Ok(json!(false)),
            Value::Number(number) if number.as_i64() == Some(1) => Ok(json!(true)),
            Value::String(text) => match text.to_lowercase().as_str() {
                "0" | "off" | "f" | "false" | "n" | "no" => Ok(json!(false)),
                "1" | "on" | "t" | "true" | "y" | "yes" => Ok(json!(true)),
                _ => Err("Input should be a valid boolean, unable to interpret input".to_string()),
            },
            _ => Err("Input should be a valid boolean, unable to interpret input".to_string()),
        },
        Kind::IntList | Kind::StrList => match value {
            Value::Array(items) => {
                let mut parsed = Vec::new();
                for (index, item) in items.iter().enumerate() {
                    parsed.push(
                        coerce(
                            if matches!(kind, Kind::StrList) {
                                Kind::Str
                            } else {
                                Kind::Int
                            },
                            item,
                        )
                        .map_err(|message| format!("{index}: {message}"))?,
                    );
                }
                Ok(Value::Array(parsed))
            }
            _ => Err("Input should be a valid list".to_string()),
        },
        Kind::Literal(choices) => match value {
            Value::String(text) if choices.contains(&text.as_str()) => Ok(value.clone()),
            _ => Err(format!("Input should be {}", join_choices(choices))),
        },
    }
}

/// The arguments as the signature promises them, defaults filled; or
/// the failures, one per field in schema order, the extras last, as
/// `describe_validation_error` joins them.
pub fn validate(
    fields: &[Field],
    arguments: &Map<String, Value>,
) -> Result<Map<String, Value>, String> {
    let mut validated = Map::new();
    let mut failures: Vec<String> = Vec::new();
    for field in fields {
        // A null is absence for an optional field and a type error otherwise.
        match (arguments.get(field.name), field.presence) {
            (None, Presence::Required) => failures.push(format!("{}: Field required", field.name)),
            (None | Some(Value::Null), Presence::Optional) => {
                validated.insert(field.name.to_string(), Value::Null);
            }
            (None, Presence::Default(text)) => {
                validated.insert(field.name.to_string(), default_value(field.kind, text));
            }
            (Some(value), _) => match coerce(field.kind, value) {
                Ok(value) => {
                    validated.insert(field.name.to_string(), value);
                }
                Err(message) => failures.push(locate(field.name, &message)),
            },
        }
    }
    for key in arguments.keys() {
        if !fields.iter().any(|field| field.name == key) {
            failures.push(format!("{key}: Extra inputs are not permitted"));
        }
    }
    if failures.is_empty() {
        Ok(validated)
    } else {
        Err(failures.join("; "))
    }
}

/// `loc` as pydantic spells it: the field, and the item index of a list.
fn locate(name: &str, message: &str) -> String {
    match message.split_once(": ") {
        Some((index, rest)) if index.bytes().all(|b| b.is_ascii_digit()) => {
            format!("{name}.{index}: {rest}")
        }
        _ => format!("{name}: {message}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const FIELDS: [Field; 5] = [
        Field {
            name: "turn",
            kind: Kind::Int,
            presence: Presence::Required,
        },
        Field {
            name: "conversation",
            kind: Kind::Str,
            presence: Presence::Optional,
        },
        Field {
            name: "limit",
            kind: Kind::Int,
            presence: Presence::Default("10"),
        },
        Field {
            name: "range",
            kind: Kind::IntList,
            presence: Presence::Optional,
        },
        Field {
            name: "mode",
            kind: Kind::Literal(&["a", "b", "c"]),
            presence: Presence::Default("a"),
        },
    ];

    fn check(arguments: Value) -> Result<Map<String, Value>, String> {
        validate(&FIELDS, arguments.as_object().unwrap())
    }

    #[test]
    fn the_schema_is_pydantics() {
        let schema = input_schema(&FIELDS, &[("turn".to_string(), "The turn.".to_string())]);
        assert_eq!(
            schema,
            json!({
                "properties": {
                    "turn": {"description": "The turn.", "type": "integer"},
                    "conversation": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": null, "description": ""},
                    "limit": {"default": 10, "description": "", "type": "integer"},
                    "range": {"anyOf": [{"items": {"type": "integer"}, "type": "array"}, {"type": "null"}], "default": null, "description": ""},
                    "mode": {"default": "a", "description": "", "enum": ["a", "b", "c"], "type": "string"},
                },
                "required": ["turn"],
                "type": "object",
                "additionalProperties": false
            })
        );
        assert!(input_schema(&[], &[]).get("required").is_none());
    }

    #[test]
    fn lax_coercion_and_the_messages_are_pydantics() {
        let ok =
            check(json!({"turn": "3", "limit": 2.0, "range": ["1", true], "mode": "b"})).unwrap();
        assert_eq!(
            ok,
            *json!({"turn": 3, "conversation": null, "limit": 2, "range": [1, 1], "mode": "b"})
                .as_object()
                .unwrap()
        );
        assert_eq!(check(json!({})).unwrap_err(), "turn: Field required");
        assert_eq!(
            check(json!({"turn": "x"})).unwrap_err(),
            "turn: Input should be a valid integer, unable to parse string as an integer"
        );
        assert_eq!(
            check(json!({"turn": 3.5})).unwrap_err(),
            "turn: Input should be a valid integer, got a number with a fractional part"
        );
        assert_eq!(
            check(json!({"turn": null})).unwrap_err(),
            "turn: Input should be a valid integer"
        );
        assert_eq!(
            check(json!({"turn": [3]})).unwrap_err(),
            "turn: Input should be a valid integer"
        );
        assert_eq!(
            check(json!({"turn": 1, "conversation": 5})).unwrap_err(),
            "conversation: Input should be a valid string"
        );
        assert_eq!(
            check(json!({"turn": 1, "range": "1"})).unwrap_err(),
            "range: Input should be a valid list"
        );
        assert_eq!(
            check(json!({"turn": 1, "range": [1, "x"]})).unwrap_err(),
            "range.1: Input should be a valid integer, unable to parse string as an integer"
        );
        assert_eq!(
            check(json!({"turn": 1, "mode": "z"})).unwrap_err(),
            "mode: Input should be 'a', 'b' or 'c'"
        );
        assert_eq!(
            check(json!({"turn": 1, "mode": 5})).unwrap_err(),
            "mode: Input should be 'a', 'b' or 'c'"
        );
        assert_eq!(
            check(json!({"turn": 1, "extra": 1})).unwrap_err(),
            "extra: Extra inputs are not permitted"
        );
        assert_eq!(
            check(json!({"limit": null, "extra": 1})).unwrap_err(),
            "turn: Field required; limit: Input should be a valid integer; extra: Extra inputs are not permitted"
        );
        let flags = [Field {
            name: "on",
            kind: Kind::Bool,
            presence: Presence::Default("false"),
        }];
        for (given, expected) in [
            (json!("yes"), true),
            (json!(1), true),
            (json!("OFF"), false),
            (json!(0), false),
        ] {
            let got = validate(&flags, json!({"on": given}).as_object().unwrap()).unwrap();
            assert_eq!(got["on"], json!(expected));
        }
        assert_eq!(
            validate(&flags, json!({"on": "maybe"}).as_object().unwrap()).unwrap_err(),
            "on: Input should be a valid boolean, unable to interpret input"
        );
        assert_eq!(
            validate(&flags, json!({"on": 2}).as_object().unwrap()).unwrap_err(),
            "on: Input should be a valid boolean, unable to interpret input"
        );
        assert_eq!(validate(&flags, &Map::new()).unwrap()["on"], json!(false));
    }
}
