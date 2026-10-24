use super::*;
use unicode_segmentation::UnicodeSegmentation;

#[derive(Clone)]
pub(super) struct Row {
    pub text: String,
    pub kind: RowKind,
    pub block: Option<usize>,
    pub decorations: Vec<super::markup::Decoration>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum RowKind {
    User,
    Assistant,
    Tool,
    Output,
    Blank,
}

pub(super) struct View {
    pub document: Document,
    pub expanded: bool,
    pub overrides: HashMap<String, bool>,
    pub rows: Vec<Row>,
    pub width: u16,
    pub dirty: bool,
    pub offset: usize,
    pub top: usize,
    pub selection: Option<((usize, u16), (usize, u16))>,
    pub dragging: bool,
    pub anchor: Option<(String, usize)>,
    pub reading_anchor: Option<(String, usize)>,
    pub pending_document: Option<Document>,
}

impl View {
    pub fn new(document: Document) -> Self {
        Self {
            document,
            expanded: false,
            overrides: HashMap::new(),
            rows: Vec::new(),
            width: 0,
            dirty: true,
            offset: 0,
            top: 0,
            selection: None,
            dragging: false,
            anchor: None,
            reading_anchor: None,
            pending_document: None,
        }
    }

    pub fn is_expanded(&self, block: &Block) -> bool {
        self.overrides
            .get(&block.id)
            .copied()
            .unwrap_or(self.expanded)
    }

    pub fn all_expanded(&self) -> bool {
        let mut tools = self
            .document
            .blocks
            .iter()
            .filter(|block| block.kind == "tool")
            .peekable();
        if tools.peek().is_none() {
            return self.expanded;
        }
        tools.all(|block| self.is_expanded(block))
    }

    pub fn toggle_all(&mut self) {
        self.expanded = !self.all_expanded();
        self.overrides.clear();
        self.dirty = true;
        self.selection = None;
        self.offset = 0;
        self.anchor = None;
        self.reading_anchor = None;
    }

    pub fn toggle(&mut self, index: usize) {
        if let Some(block) = self.document.blocks.get(index) {
            self.overrides
                .insert(block.id.clone(), !self.is_expanded(block));
            self.dirty = true;
            self.selection = None;
        }
    }

    pub fn update(&mut self, document: Document) {
        if self.document.session_id != document.session_id {
            *self = Self::new(document);
        } else if self.document.revision != document.revision {
            if self.offset > 0 {
                if let Some(block) =
                    self.rows
                        .get(self.top)
                        .and_then(|row| row.block)
                        .or_else(|| {
                            self.rows
                                .iter()
                                .take(self.top + 1)
                                .rev()
                                .find_map(|row| row.block)
                        })
                {
                    if let Some(origin) = self.rows.iter().position(|row| row.block == Some(block))
                    {
                        self.reading_anchor = self
                            .document
                            .blocks
                            .get(block)
                            .map(|block| (block.id.clone(), self.top.saturating_sub(origin)));
                    }
                }
            }
            self.document = document;
            self.dirty = true;
        }
    }

    // Wrap only after content, folding or width changes, never once per pane/frame.
    pub fn prepare(&mut self, width: u16) {
        if !self.dirty && self.width == width {
            return;
        }
        // Preserve a selected range by keeping its rows stable until selection ends.
        if self.dragging && self.width == width {
            return;
        }
        self.width = width;
        self.dirty = false;
        self.rows.clear();
        let width = usize::from(width.max(1));
        for (index, block) in self.document.blocks.iter().enumerate() {
            let kind = match block.kind.as_str() {
                "user" => RowKind::User,
                "assistant" => RowKind::Assistant,
                _ => RowKind::Tool,
            };
            if kind == RowKind::Tool {
                let marker = if self.is_expanded(block) {
                    "▾"
                } else {
                    "▸"
                };
                let state = if block.pending { " · running" } else { "" };
                self.rows.push(Row {
                    text: format!("{marker} {}{state}", block.summary),
                    kind,
                    block: Some(index),
                    decorations: Vec::new(),
                });
                if !self.is_expanded(block) {
                    continue;
                }
                for text in [&block.text, &block.output] {
                    if text.is_empty() {
                        continue;
                    }
                    for line in super::markup::code_wrap(text, width.saturating_sub(2).max(1)) {
                        self.rows.push(Row {
                            text: format!("  {}", line.text),
                            kind: RowKind::Output,
                            block: None,
                            decorations: line.decorations,
                        });
                    }
                }
            } else if kind == RowKind::Assistant {
                for (n, line) in super::markup::wrap(&block.text, width.saturating_sub(2).max(1))
                    .into_iter()
                    .enumerate()
                {
                    self.rows.push(Row {
                        text: format!(
                            "{}{line}",
                            if n == 0 { "• " } else { "  " },
                            line = line.text
                        ),
                        kind,
                        block: Some(index),
                        decorations: line.decorations,
                    });
                }
            } else {
                let prefix = if kind == RowKind::User {
                    "› "
                } else {
                    "• "
                };
                for (n, line) in wrap(&block.text, width.saturating_sub(2).max(1))
                    .into_iter()
                    .enumerate()
                {
                    self.rows.push(Row {
                        text: format!("{}{line}", if n == 0 { prefix } else { "  " }),
                        kind,
                        block: Some(index),
                        decorations: Vec::new(),
                    });
                }
            }
            self.rows.push(Row {
                text: String::new(),
                kind: RowKind::Blank,
                block: None,
                decorations: Vec::new(),
            });
        }
        // A changed row map invalidates old coordinates; do not select unrelated text.
        self.selection = None;
    }

    pub fn selected_text(&self) -> Option<String> {
        let (mut start, mut end) = self.selection?;
        if start > end {
            std::mem::swap(&mut start, &mut end);
        }
        if start == end {
            return None;
        }
        let mut text = String::new();
        for index in start.0..=end.0.min(self.rows.len().saturating_sub(1)) {
            let row = self.rows.get(index)?;
            let from = if index == start.0 {
                usize::from(start.1)
            } else {
                0
            };
            let to = if index == end.0 {
                usize::from(end.1) + 1
            } else {
                usize::MAX
            };
            let mut col = 0;
            for glyph in row.text.graphemes(true) {
                let next = col + UnicodeWidthStr::width(glyph);
                if next > from && col < to {
                    text.push_str(glyph);
                }
                col = next;
            }
            if index != end.0 {
                text.push('\n');
            }
        }
        (!text.trim().is_empty() && text.len() <= 131_072).then_some(text)
    }
}

pub(super) fn wrap(text: &str, width: usize) -> Vec<String> {
    let mut rows = Vec::new();
    for line in text.lines() {
        let mut row = String::new();
        let mut used = 0;
        for glyph in line.graphemes(true) {
            let glyph = if glyph == "\t" { "    " } else { glyph };
            let cells = UnicodeWidthStr::width(glyph);
            if used > 0 && used + cells > width {
                rows.push(std::mem::take(&mut row));
                used = 0;
            }
            row.push_str(glyph);
            used += cells;
        }
        rows.push(row);
    }
    if rows.is_empty() {
        rows.push(String::new());
    }
    rows
}
