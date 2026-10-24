//! Small cached lexical highlighter for conversation code and expanded tool text.
//! Uses the terminal's ANSI palette, so colors follow the user's Terminal profile.
use ratatui::style::Color;
use regex::Regex;
use std::sync::OnceLock;

pub(super) fn tokens(line: &str) -> Vec<(&str, Option<Color>)> {
    static LEXER: OnceLock<Regex> = OnceLock::new();
    let lexer = LEXER.get_or_init(|| {
        Regex::new(
            r###"(?x)
        (?P<comment>//[^\n]*|\#[^\n]*|/\*.*?\*/)
        |(?P<string>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)
        |(?P<number>\b(?:0[xX][0-9a-fA-F_]+|[0-9][0-9_]*(?:\.[0-9_]+)?)\b)
        |(?P<variable>\$\{?\w+\}?)
        |(?P<word>[\p{L}_][\p{L}\p{N}_]*)
    "###,
        )
        .expect("static syntax lexer")
    });
    let mut result = Vec::new();
    let mut end = 0;
    for capture in lexer.captures_iter(line) {
        let token = capture.get(0).unwrap();
        if token.start() > end {
            result.push((&line[end..token.start()], None));
        }
        let word = token.as_str();
        let color = if capture.name("comment").is_some() {
            Some(Color::DarkGray)
        } else if capture.name("string").is_some() {
            Some(Color::Green)
        } else if capture.name("number").is_some() {
            Some(Color::Yellow)
        } else if capture.name("variable").is_some() {
            Some(Color::Cyan)
        } else if matches!(
            word,
            "as" | "async"
                | "await"
                | "break"
                | "case"
                | "catch"
                | "class"
                | "const"
                | "continue"
                | "def"
                | "default"
                | "defer"
                | "del"
                | "do"
                | "elif"
                | "else"
                | "enum"
                | "except"
                | "export"
                | "extends"
                | "finally"
                | "fn"
                | "for"
                | "from"
                | "func"
                | "function"
                | "if"
                | "impl"
                | "import"
                | "in"
                | "interface"
                | "is"
                | "lambda"
                | "let"
                | "match"
                | "mod"
                | "move"
                | "mut"
                | "new"
                | "not"
                | "of"
                | "or"
                | "and"
                | "package"
                | "pass"
                | "private"
                | "pub"
                | "public"
                | "raise"
                | "ref"
                | "return"
                | "static"
                | "struct"
                | "super"
                | "switch"
                | "then"
                | "this"
                | "throw"
                | "trait"
                | "try"
                | "type"
                | "typeof"
                | "unsafe"
                | "use"
                | "var"
                | "where"
                | "while"
                | "with"
                | "yield"
                | "SELECT"
                | "FROM"
                | "WHERE"
                | "INSERT"
                | "UPDATE"
                | "DELETE"
                | "JOIN"
                | "INTO"
                | "VALUES"
                | "ORDER"
                | "BY"
                | "LIMIT"
                | "CREATE"
                | "TABLE"
        ) {
            Some(Color::Magenta)
        } else if matches!(
            word,
            "true" | "false" | "null" | "nil" | "None" | "True" | "False" | "undefined" | "self"
        ) {
            Some(Color::Yellow)
        } else if line[token.end()..].trim_start().starts_with('(') {
            Some(Color::Blue)
        } else if word.chars().next().is_some_and(char::is_uppercase) {
            Some(Color::Cyan)
        } else {
            None
        };
        result.push((word, color));
        end = token.end();
    }
    if end < line.len() {
        result.push((&line[end..], None));
    }
    result
}
