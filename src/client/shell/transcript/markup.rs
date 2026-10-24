//! Visible assistant Markdown and its cell ranges, cached with conversation rows.
use super::*;
use unicode_segmentation::UnicodeSegmentation;

#[derive(Clone, Copy)]
pub(super) struct Decoration {
    pub start: u16,
    pub end: u16,
    pub modifiers: Modifier,
    pub code: bool,
    pub fg: Option<ratatui::style::Color>,
}

pub(super) struct StyledLine {
    pub text: String,
    pub decorations: Vec<Decoration>,
}

struct Piece {
    text: String,
    modifiers: Modifier,
    code: bool,
    fg: Option<ratatui::style::Color>,
}

pub(super) fn visible(text: &str) -> String {
    static CITATIONS: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    CITATIONS
        .get_or_init(|| {
            regex::Regex::new(r"(?is)<oai-mem-citation\b[^>]*>.*?(?:</oai-mem-citation\s*>|$)")
                .expect("static citation regex")
        })
        .replace_all(text, "")
        .trim_end()
        .to_owned()
}

fn inline(text: &str, modifiers: Modifier) -> Vec<Piece> {
    let mut pieces = Vec::new();
    let mut plain = String::new();
    let flush = |plain: &mut String, pieces: &mut Vec<Piece>| {
        if !plain.is_empty() {
            pieces.push(Piece {
                text: std::mem::take(plain),
                modifiers,
                code: false,
                fg: None,
            });
        }
    };
    let mut index = 0;
    while index < text.len() {
        let rest = &text[index..];
        if let Some(escaped) = rest.strip_prefix('\\').and_then(|rest| rest.chars().next()) {
            if "\\`*_[]#".contains(escaped) {
                plain.push(escaped);
                index += 1 + escaped.len_utf8();
                continue;
            }
        }
        if rest.starts_with('`') {
            let count = rest.bytes().take_while(|byte| *byte == b'`').count();
            let delimiter = &rest[..count];
            if let Some(end) = rest[count..].find(delimiter) {
                flush(&mut plain, &mut pieces);
                pieces.push(Piece {
                    text: rest[count..count + end].to_owned(),
                    modifiers,
                    code: true,
                    fg: None,
                });
                index += count + end + count;
                continue;
            }
        }
        let delimiter = ["**", "__", "*", "_"]
            .into_iter()
            .find(|delimiter| rest.starts_with(delimiter));
        if let Some(delimiter) = delimiter {
            let size = delimiter.len();
            if let Some(end) = rest[size..].find(delimiter).filter(|end| *end > 0) {
                let content = &rest[size..size + end];
                if !content.starts_with(char::is_whitespace)
                    && !content.ends_with(char::is_whitespace)
                {
                    flush(&mut plain, &mut pieces);
                    let extra = if size == 2 {
                        Modifier::BOLD
                    } else {
                        Modifier::ITALIC
                    };
                    pieces.extend(inline(content, modifiers | extra));
                    index += size + end + size;
                    continue;
                }
            }
        }
        let ch = rest.chars().next().expect("nonempty slice");
        plain.push(ch);
        index += ch.len_utf8();
    }
    flush(&mut plain, &mut pieces);
    pieces
}

fn lines(text: &str) -> Vec<Vec<Piece>> {
    let text = visible(text);
    let mut result = Vec::new();
    let mut fence = None;
    for line in text.lines() {
        let trimmed = line.trim_start();
        if trimmed.starts_with("```") || trimmed.starts_with("~~~") {
            let delimiter = &trimmed[..3];
            if fence.is_none() {
                fence = Some(delimiter.to_owned());
                continue;
            }
            if fence.as_deref() == Some(delimiter) {
                fence = None;
                continue;
            }
        }
        if fence.is_some() {
            result.push(code_pieces(line));
        } else {
            let hashes = trimmed.bytes().take_while(|byte| *byte == b'#').count();
            if (1..=6).contains(&hashes) && trimmed.as_bytes().get(hashes) == Some(&b' ') {
                result.push(inline(
                    trimmed[hashes + 1..].trim_end_matches('#').trim_end(),
                    Modifier::BOLD,
                ));
            } else {
                result.push(inline(line, Modifier::empty()));
            }
        }
    }
    result
}

pub(super) fn wrap(text: &str, width: usize) -> Vec<StyledLine> {
    wrap_pieces(lines(text), width)
}

pub(super) fn code_wrap(text: &str, width: usize) -> Vec<StyledLine> {
    wrap_pieces(text.lines().map(code_pieces).collect(), width)
}

fn code_pieces(line: &str) -> Vec<Piece> {
    super::syntax::tokens(line)
        .into_iter()
        .map(|(text, fg)| Piece {
            text: text.to_owned(),
            modifiers: Modifier::empty(),
            code: false,
            fg,
        })
        .collect()
}

fn wrap_pieces(lines: Vec<Vec<Piece>>, width: usize) -> Vec<StyledLine> {
    let mut rows = Vec::new();
    for pieces in lines {
        let mut row = StyledLine {
            text: String::new(),
            decorations: Vec::new(),
        };
        let mut used = 0;
        for piece in pieces {
            for glyph in piece.text.graphemes(true) {
                let glyph = if glyph == "\t" { "    " } else { glyph };
                let cells = UnicodeWidthStr::width(glyph);
                if used > 0 && used + cells > width {
                    rows.push(row);
                    row = StyledLine {
                        text: String::new(),
                        decorations: Vec::new(),
                    };
                    used = 0;
                }
                row.text.push_str(glyph);
                if piece.code || piece.fg.is_some() || !piece.modifiers.is_empty() {
                    let start = used.min(u16::MAX as usize) as u16;
                    let end = (used + cells).min(u16::MAX as usize) as u16;
                    if let Some(last) = row.decorations.last_mut().filter(|last| {
                        last.end == start
                            && last.modifiers == piece.modifiers
                            && last.code == piece.code
                            && last.fg == piece.fg
                    }) {
                        last.end = end;
                    } else {
                        row.decorations.push(Decoration {
                            start,
                            end,
                            modifiers: piece.modifiers,
                            code: piece.code,
                            fg: piece.fg,
                        });
                    }
                }
                used += cells;
            }
        }
        rows.push(row);
    }
    rows
}
