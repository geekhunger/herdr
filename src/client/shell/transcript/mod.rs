//! Client presentation of typed Codex messages. The terminal and server stay intact.
use super::*;
use crossterm::event::{KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use serde::{Deserialize, Serialize};
use std::time::{Duration, Instant};

mod copy;
mod markup;
mod reader;
mod syntax;
mod view;
use view::{RowKind, View};

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub(super) struct Target {
    pane_id: String,
    boot_id: String,
    title: String,
    cwd: String,
}

#[derive(Clone, Debug, Deserialize)]
pub(super) struct Block {
    id: String,
    kind: String,
    text: String,
    summary: String,
    output: String,
    pending: bool,
}

#[derive(Clone, Debug, Deserialize)]
pub(super) struct Document {
    session_id: String,
    revision: u64,
    blocks: Vec<Block>,
}

#[derive(Default)]
pub(super) struct Transcript {
    worker: Option<reader::Worker>,
    target: Option<Target>,
    checked_at: Option<Instant>,
    views: HashMap<String, View>,
    view_targets: HashMap<String, Target>,
    pub area: Rect,
    body: Rect,
    active_pane: Option<String>,
    pub(in crate::client::shell) copy: Option<copy::CopyCursor>,
}

impl ClientShellState {
    fn transcript_target(&self) -> Option<Target> {
        if !self.active_endpoint_id.is_local() {
            return None;
        }
        let snapshot = self.snapshot.as_ref()?;
        let pane_id = snapshot.focused_pane_id.as_ref()?;
        let agent = snapshot.agents.iter().find(|agent| {
            agent.pane_id == *pane_id
                && agent
                    .agent
                    .as_deref()
                    .is_some_and(|kind| kind.eq_ignore_ascii_case("codex"))
        })?;
        let title = agent
            .terminal_title_stripped
            .as_ref()
            .or(agent.title.as_ref())?;
        let pane = snapshot
            .panes
            .iter()
            .find(|pane| pane.pane_id == *pane_id)?;
        let cwd = pane.foreground_cwd.as_ref().or(pane.cwd.as_ref())?;
        Some(Target {
            pane_id: pane_id.clone(),
            boot_id: snapshot.boot_id.clone(),
            title: title.clone(),
            cwd: cwd.clone(),
        })
    }

    pub(crate) fn tick_transcript(&mut self, now: Instant) -> bool {
        let mut repaint = false;
        if self.copy_mode.is_none() {
            self.transcript.copy = None;
        }
        if let Some(worker) = self.transcript.worker.as_ref() {
            while let Ok((target, document)) = worker.results.try_recv() {
                if self.transcript.target.as_ref() != Some(&target) {
                    continue;
                }
                if let Some(document) = document.filter(|document| !document.blocks.is_empty()) {
                    if let Some(view) = self.transcript.views.get_mut(&target.pane_id) {
                        if self
                            .transcript
                            .copy
                            .as_ref()
                            .is_some_and(|cursor| cursor.pane_id == target.pane_id)
                        {
                            view.pending_document = Some(document);
                        } else {
                            view.update(document);
                        }
                    } else {
                        if self.transcript.views.len() >= 8 {
                            self.transcript.views.clear();
                            self.transcript.view_targets.clear();
                        }
                        self.transcript
                            .view_targets
                            .insert(target.pane_id.clone(), target.clone());
                        self.transcript
                            .views
                            .insert(target.pane_id.clone(), View::new(document));
                    }
                } else {
                    self.transcript.views.remove(&target.pane_id);
                    self.transcript.view_targets.remove(&target.pane_id);
                }
                repaint = true;
            }
        }
        if self
            .transcript
            .checked_at
            .is_some_and(|last| now.saturating_duration_since(last) < Duration::from_millis(500))
        {
            return repaint;
        }
        self.transcript.checked_at = Some(now);
        let target = self.transcript_target();
        if self.transcript.target != target {
            if self.transcript.copy.is_some() {
                self.exit_transcript_copy(false, &mut ClientShellInput::default());
            }
            self.transcript.area = Rect::default();
            if let Some(target) = &target {
                // Reuse only the exact pane/session identity. Switching tabs
                // must preserve folding and reading position; reusing a pane
                // for another conversation must never expose its old content.
                if self.transcript.view_targets.get(&target.pane_id) != Some(target) {
                    self.transcript.views.remove(&target.pane_id);
                    self.transcript.view_targets.remove(&target.pane_id);
                }
            }
            self.transcript.target = target.clone();
            repaint = true;
        }
        if let Some(target) = target {
            if self.transcript.worker.is_none() {
                self.transcript.worker = reader::Worker::start();
            }
            if let Some(worker) = &self.transcript.worker {
                if matches!(
                    worker.requests.try_send(target),
                    Err(std::sync::mpsc::TrySendError::Disconnected(_))
                ) {
                    self.transcript.worker = None;
                }
            }
        }
        repaint
    }

    pub(super) fn transcript_visible(&self) -> bool {
        if !self.active_endpoint_id.is_local()
            || !(matches!(
                self.mode,
                ClientShellMode::Terminal | ClientShellMode::Prefix
            ) || (self.mode == ClientShellMode::Copy && self.transcript.copy.is_some()))
            || self.popup_terminal_id.is_some()
            || self.popup_pending
        {
            return false;
        }
        let Some(target) = self.transcript.target.as_ref() else {
            return false;
        };
        let Some(snapshot) = self.snapshot.as_ref() else {
            return false;
        };
        snapshot.boot_id == target.boot_id
            && snapshot.focused_pane_id.as_ref() == Some(&target.pane_id)
            && snapshot.agents.iter().any(|agent| {
                agent.pane_id == target.pane_id
                    && agent
                        .agent
                        .as_deref()
                        .is_some_and(|kind| kind.eq_ignore_ascii_case("codex"))
                    && agent
                        .terminal_title_stripped
                        .as_ref()
                        .or(agent.title.as_ref())
                        == Some(&target.title)
            })
            && self.transcript.views.contains_key(&target.pane_id)
    }

    pub(super) fn route_transcript_key(
        &mut self,
        key: &crate::input::TerminalKey,
        outcome: &mut ClientShellInput,
    ) -> bool {
        if self.mode != ClientShellMode::Terminal
            || !self.transcript_visible()
            || self.transcript.area.is_empty()
        {
            return false;
        }
        if self.selection.take().is_some() {
            self.stop_selection_autoscroll();
        }
        let pane_id = self
            .transcript
            .target
            .as_ref()
            .expect("visible target")
            .pane_id
            .clone();
        let view = self
            .transcript
            .views
            .get_mut(&pane_id)
            .expect("visible view");
        if key.code == KeyCode::Char('t') && key.modifiers == KeyModifiers::CONTROL {
            view.toggle_all();
            self.reply_selection = None;
            outcome.repaint = true;
            return true;
        }
        if key.code == KeyCode::PageUp || key.code == KeyCode::PageDown {
            let step = usize::from(self.transcript.body.height.saturating_sub(2).max(1));
            view.offset = if key.code == KeyCode::PageUp {
                view.offset.saturating_add(step)
            } else {
                view.offset.saturating_sub(step)
            };
            view.selection = None;
            self.reply_selection = None;
            outcome.repaint = true;
            return true;
        }
        if matches!(key.code, KeyCode::Char('c' | 'C'))
            && matches!(key.modifiers, KeyModifiers::CONTROL | KeyModifiers::SUPER)
        {
            if let Some(text) = view.selected_text() {
                outcome
                    .actions
                    .push(ClientShellAction::ClipboardWrite(text.into_bytes()));
                return true;
            }
        }
        if key.code == KeyCode::Esc && view.selection.take().is_some() {
            self.reply_selection = None;
            outcome.repaint = true;
            return true;
        }
        if view.selection.take().is_some() {
            outcome.repaint = true;
        }
        false
    }

    fn cache_transcript_reply(&mut self) -> Option<String> {
        let pane_id = self.transcript.active_pane.as_ref()?;
        let text = self.transcript.views.get(pane_id)?.selected_text()?;
        self.reply_selection = Some(super::reply_selection::ReplySelection {
            endpoint_id: self.active_endpoint_id.clone(),
            boot_id: self.snapshot.as_ref()?.boot_id.clone(),
            pane_id: pane_id.clone(),
            request_id: "transcript".into(),
            text: Some(text.clone()),
            requested: false,
        });
        Some(text)
    }

    pub(super) fn route_transcript_mouse(
        &mut self,
        mouse: MouseEvent,
        outcome: &mut ClientShellInput,
    ) -> bool {
        if self.overlay.is_some() || !self.transcript_visible() || self.transcript.area.is_empty() {
            return false;
        }
        if contains(self.transcript.area, (mouse.column, mouse.row))
            && matches!(
                mouse.kind,
                MouseEventKind::Down(_) | MouseEventKind::ScrollUp | MouseEventKind::ScrollDown
            )
        {
            self.selection = None;
            self.stop_selection_autoscroll();
            self.word_selection_gesture = None;
            self.last_pane_click = None;
        }
        if mouse.kind == MouseEventKind::Down(MouseButton::Left) {
            if let Some(request) = self.transcript.attachment_request(mouse) {
                match crate::platform::open_image_preview(&request.to_string()) {
                    Ok(true) => {}
                    Ok(false) => self.set_endpoint_error("Image preview helper is not installed"),
                    Err(error) => self.set_endpoint_error(format!("Image preview: {error}")),
                }
                self.reply_selection = None;
                outcome.repaint = true;
                return true;
            }
        }
        let point = (mouse.column, mouse.row);
        let pane_id = self
            .transcript
            .active_pane
            .as_ref()
            .expect("painted pane")
            .clone();
        let view = self
            .transcript
            .views
            .get_mut(&pane_id)
            .expect("painted view");
        if !(contains(self.transcript.area, point)
            || view.dragging
                && matches!(
                    mouse.kind,
                    MouseEventKind::Drag(MouseButton::Left) | MouseEventKind::Up(MouseButton::Left)
                ))
        {
            return false;
        }
        let body = self.transcript.body;
        let row = usize::from(mouse.row.clamp(body.y, body.bottom() - 1) - body.y) + view.top;
        let col = mouse
            .column
            .saturating_sub(body.x)
            .min(body.width.saturating_sub(2));
        if let Some(cursor) = self.transcript.copy.as_mut() {
            if matches!(
                mouse.kind,
                MouseEventKind::Down(MouseButton::Left) | MouseEventKind::Drag(MouseButton::Left)
            ) {
                cursor.row = row;
                cursor.col = col;
                cursor.reveal = false;
            }
        }
        match mouse.kind {
            MouseEventKind::ScrollUp | MouseEventKind::ScrollDown => {
                view.offset = if mouse.kind == MouseEventKind::ScrollUp {
                    view.offset.saturating_add(3)
                } else {
                    view.offset.saturating_sub(3)
                };
                view.selection = None;
                self.reply_selection = None;
            }
            MouseEventKind::Down(MouseButton::Left) if mouse.row == self.transcript.area.y => {
                view.toggle_all();
                self.reply_selection = None;
            }
            MouseEventKind::Down(MouseButton::Left) => {
                self.reply_selection = None;
                self.selection = None;
                if let Some(index) = view
                    .rows
                    .get(row)
                    .filter(|row| row.kind == RowKind::Tool)
                    .and_then(|row| row.block)
                {
                    view.anchor = Some((view.document.blocks[index].id.clone(), row - view.top));
                    view.toggle(index);
                } else {
                    view.selection = Some(((row, col), (row, col)));
                    view.dragging = true;
                }
            }
            MouseEventKind::Drag(MouseButton::Left) => {
                if let Some((_, end)) = view.selection.as_mut() {
                    *end = (row, col);
                }
            }
            MouseEventKind::Up(MouseButton::Left) if view.dragging => {
                view.dragging = false;
                if let Some(text) = self.cache_transcript_reply() {
                    if self.config.copy_on_select {
                        outcome
                            .actions
                            .push(ClientShellAction::ClipboardWrite(text.into_bytes()));
                    }
                }
            }
            MouseEventKind::Down(MouseButton::Right) => {
                self.cache_transcript_reply();
                self.open_pane_context_menu(pane_id, mouse.column, mouse.row);
            }
            _ => return true,
        }
        outcome.repaint = true;
        true
    }
}

impl Transcript {
    fn attachment_request(&self, mouse: MouseEvent) -> Option<serde_json::Value> {
        if !contains(self.body, (mouse.column, mouse.row)) {
            return None;
        }
        let target = self.target.as_ref()?;
        let view = self.views.get(&target.pane_id)?;
        let row = view
            .rows
            .get(view.top + usize::from(mouse.row - self.body.y))?;
        if row.kind != RowKind::User {
            return None;
        }
        let byte_col = byte_column(&row.text, usize::from(mouse.column - self.body.x));
        let regex = regex::Regex::new(r"\[(Image|Video) #(\d+)\]").ok()?;
        let capture = regex.captures_iter(&row.text).find(|capture| {
            capture
                .get(0)
                .is_some_and(|found| found.start() <= byte_col && byte_col < found.end())
        })?;
        let index: usize = capture.get(2)?.as_str().parse().ok()?;
        if !(1..=100).contains(&index) {
            return None;
        }
        if capture.get(1)?.as_str() == "Video" {
            return Some(
                serde_json::json!({"media":"video", "index":index, "pane":target.pane_id, "socket":crate::api::socket_path()}),
            );
        }
        let block = view.document.blocks.get(row.block?)?;
        Some(
            serde_json::json!({"index":index,"context":block.text,"title":target.title,"cwd":target.cwd,"pane":target.pane_id}),
        )
    }

    pub(super) fn render(
        &mut self,
        visible: bool,
        blocked: bool,
        surface: &PaneSurfaceFrame,
        hits: &ShellHitMap,
        palette: &Palette,
        frame: &mut FrameData,
    ) -> Option<Rect> {
        self.area = Rect::default();
        if !visible {
            return None;
        }
        let pane_id = self.target.as_ref()?.pane_id.clone();
        let source = surface.panes.iter().find(|pane| pane.pane_id == pane_id)?;
        let hit = hits.panes.iter().find(|hit| hit.pane_id == pane_id)?;
        let view = self.views.get_mut(&pane_id)?;
        view.prepare(hit.inner_rect.width.saturating_sub(1));
        let height = history_height(surface, source, blocked)?;
        let area = Rect::new(
            hit.inner_rect.x,
            hit.inner_rect.y,
            hit.inner_rect.width,
            height.min(hit.inner_rect.height),
        );
        if area.height < 4 || area.width < 16 {
            return None;
        }
        let body = Rect::new(area.x, area.y + 1, area.width, area.height - 1);
        let geometry_anchor =
            (view.offset > 0 && self.body.height > 0 && self.body.height != body.height)
                .then_some(view.top);
        let has_reading_anchor = view.reading_anchor.is_some() || view.anchor.is_some();
        view.prepare(body.width.saturating_sub(1));
        let max_scroll = view.rows.len().saturating_sub(usize::from(body.height));
        if !has_reading_anchor {
            if let Some(top) = geometry_anchor {
                view.offset = max_scroll.saturating_sub(top);
            }
        }
        if let Some((id, within)) = view.reading_anchor.take() {
            if let Some(index) = view.rows.iter().position(|row| {
                row.block
                    .and_then(|n| view.document.blocks.get(n))
                    .is_some_and(|block| block.id == id)
            }) {
                view.offset = max_scroll.saturating_sub(index.saturating_add(within));
            }
        }
        if let Some((id, screen_row)) = view.anchor.take() {
            if let Some(index) = view.rows.iter().position(|row| {
                row.kind == RowKind::Tool
                    && row
                        .block
                        .and_then(|n| view.document.blocks.get(n))
                        .is_some_and(|block| block.id == id)
            }) {
                view.offset = max_scroll.saturating_sub(index.saturating_sub(screen_row));
            }
        }
        view.offset = view.offset.min(max_scroll);
        if let Some(cursor) = self
            .copy
            .as_mut()
            .filter(|cursor| cursor.pane_id == pane_id)
        {
            cursor.row = cursor.row.min(view.rows.len().saturating_sub(1));
            cursor.col = cursor.col.min(body.width.saturating_sub(2));
            if cursor.reveal {
                let top = max_scroll.saturating_sub(view.offset);
                let top = if cursor.row < top {
                    cursor.row
                } else if cursor.row >= top + usize::from(body.height) {
                    cursor
                        .row
                        .saturating_sub(usize::from(body.height.saturating_sub(1)))
                } else {
                    top
                };
                view.offset = max_scroll.saturating_sub(top);
                cursor.reveal = false;
            }
        }
        view.top = max_scroll.saturating_sub(view.offset);
        let mut buffer = frame.to_ratatui_buffer()?;
        let covered = Rect::new(area.x, area.y, area.width, area.height + 4);
        let base = native_style(
            &buffer,
            Rect::new(area.x, covered.bottom().saturating_sub(1), area.width, 0),
        );
        let statuses: Vec<_> = (covered.bottom().saturating_sub(8).max(area.y)..covered.bottom())
            .filter_map(|y| {
                let text: String = (area.x..area.right())
                    .map(|x| buffer[(x, y)].symbol())
                    .collect();
                let text = text.trim_start().trim_start_matches('•').trim_start();
                [
                    "Working",
                    "Thinking",
                    "Planning",
                    "Running",
                    "Waiting",
                    "Reconnecting",
                    "Worked for",
                    "└ Tip:",
                    "Tip:",
                ]
                .iter()
                .any(|prefix| text.starts_with(prefix))
                .then(|| {
                    (area.x..area.right())
                        .map(|x| buffer[(x, y)].clone())
                        .collect::<Vec<_>>()
                })
            })
            .rev()
            .take(4)
            .collect();
        for y in covered.y..covered.bottom() {
            for x in area.x..area.right() {
                buffer[(x, y)].reset();
                buffer[(x, y)].set_symbol(" ").set_style(base);
            }
        }
        for (row, cells) in statuses.iter().rev().enumerate() {
            for (col, cell) in cells.iter().enumerate() {
                buffer[(area.x + col as u16, area.bottom() + row as u16)] = cell.clone();
            }
        }
        let header = if view.all_expanded() {
            " Tools expanded · Ctrl+T collapse all"
        } else {
            " Tools collapsed · Ctrl+T expand all"
        };
        render::put_text(
            &mut buffer,
            area.x,
            area.y,
            area.width,
            header,
            base.add_modifier(Modifier::DIM),
        );
        for (screen_row, row) in view
            .rows
            .iter()
            .skip(view.top)
            .take(usize::from(body.height))
            .enumerate()
        {
            let y = body.y + screen_row as u16;
            let style = match row.kind {
                RowKind::User => base,
                RowKind::Tool => base.add_modifier(Modifier::DIM),
                RowKind::Output => base.fg(if row.text.trim_start().starts_with('+') {
                    palette.green
                } else if row.text.trim_start().starts_with('-') {
                    palette.red
                } else {
                    palette.overlay0
                }),
                _ => base,
            };
            render::put_text(
                &mut buffer,
                body.x,
                y,
                if row.kind == RowKind::Tool && body.width >= 32 {
                    body.width.saturating_sub(15)
                } else {
                    body.width.saturating_sub(1)
                },
                &row.text,
                style,
            );
            for decoration in &row.decorations {
                let decorated = if let Some(color) = decoration.fg {
                    base.fg(color)
                } else if decoration.code {
                    base.fg(ratatui::style::Color::Magenta)
                } else {
                    base
                };
                for col in decoration.start.saturating_add(2)
                    ..decoration
                        .end
                        .saturating_add(2)
                        .min(body.width.saturating_sub(1))
                {
                    buffer[(body.x + col, y)]
                        .set_style(decorated.add_modifier(decoration.modifiers));
                }
            }
            if row.kind == RowKind::Tool && body.width >= 32 {
                let expanded = row
                    .block
                    .and_then(|index| view.document.blocks.get(index))
                    .is_some_and(|block| view.is_expanded(block));
                render::put_text(
                    &mut buffer,
                    body.right().saturating_sub(13),
                    y,
                    12,
                    if expanded {
                        "Hide details"
                    } else {
                        "Show details"
                    },
                    base.fg(palette.accent),
                );
            }
            if let Some((mut start, mut end)) = view.selection {
                if start > end {
                    std::mem::swap(&mut start, &mut end);
                }
                let index = view.top + screen_row;
                if (start.0..=end.0).contains(&index) && start != end {
                    let left = if index == start.0 { start.1 } else { 0 };
                    let right = if index == end.0 {
                        end.1.saturating_add(1)
                    } else {
                        body.width.saturating_sub(1)
                    };
                    for col in left..right.min(body.width.saturating_sub(1)) {
                        buffer[(body.x + col, y)].set_style(
                            base.fg(base.bg.unwrap_or(palette.panel_bg))
                                .bg(palette.accent),
                        );
                    }
                }
            }
        }
        if max_scroll > 0 {
            super::scroll::render_list_scrollbar(
                &mut buffer,
                Rect::new(body.right() - 1, body.y, 1, body.height),
                crate::pane::ScrollMetrics {
                    offset_from_bottom: view.offset,
                    max_offset_from_bottom: max_scroll,
                    viewport_rows: usize::from(body.height),
                },
                palette,
            );
        }
        if let Some(cursor) = self.copy.as_ref().filter(|cursor| {
            cursor.pane_id == pane_id
                && cursor.row >= view.top
                && cursor.row < view.top + usize::from(body.height)
        }) {
            let x = body.x + cursor.col;
            let y = body.y + (cursor.row - view.top) as u16;
            buffer[(x, y)].set_style(
                base.fg(base.bg.unwrap_or(palette.panel_bg))
                    .bg(palette.accent)
                    .add_modifier(Modifier::BOLD),
            );
        }
        let cursor = frame
            .cursor
            .clone()
            .filter(|cursor| self.copy.is_none() && !contains(area, (cursor.x, cursor.y)));
        frame.replace_from_ratatui_buffer_preserving_effects(&buffer, cursor);
        self.area = area;
        self.body = body;
        self.active_pane = Some(pane_id);
        Some(covered)
    }
}

fn byte_column(text: &str, cell: usize) -> usize {
    use unicode_segmentation::UnicodeSegmentation;
    let mut width = 0;
    for (byte, glyph) in text.grapheme_indices(true) {
        let next = width + UnicodeWidthStr::width(glyph);
        if cell < next {
            return byte;
        }
        width = next;
    }
    text.len()
}

fn native_style(buffer: &Buffer, area: Rect) -> Style {
    // The retained row immediately below the history is the original status
    // line or its blank separator. Diff backgrounds inside history are not the
    // terminal theme and must never become the whole conversation background.
    let row = area.bottom().min(buffer.area.bottom().saturating_sub(1));
    let reference = (area.x..area.right())
        .rev()
        .filter_map(|x| buffer.cell((x, row)))
        .find(|cell| cell.symbol().trim().is_empty())
        .or_else(|| buffer.cell((area.x, row)));
    reference.map_or(Style::default(), |cell| {
        Style::default().fg(cell.fg).bg(cell.bg)
    })
}

// Keep the original composer and nearby status/approval UI. Never invent input state.
fn history_height(
    surface: &PaneSurfaceFrame,
    pane: &crate::protocol::PaneSurfacePane,
    blocked: bool,
) -> Option<u16> {
    let area = pane.inner_rect;
    let width = usize::from(surface.frame.width);
    let first = |row: u16| {
        let start = usize::from(row) * width + usize::from(area.x);
        surface
            .frame
            .cells
            .get(start..start + usize::from(area.width))?
            .iter()
            .find(|cell| !cell.symbol.trim().is_empty())
            .map(|cell| cell.symbol.as_str())
    };
    let prompt = (area.y..area.y.saturating_add(area.height))
        .rev()
        .take(32)
        .find(|row| first(*row) == Some("›"))?;
    let start = usize::from(prompt) * width + usize::from(area.x);
    let composer: String = surface
        .frame
        .cells
        .get(start..start + usize::from(area.width))?
        .iter()
        .map(|cell| cell.symbol.as_str())
        .collect();
    let composer = composer.trim_start().strip_prefix('›')?.trim_start();
    if composer.starts_with('/')
        || composer.starts_with('@')
        || (blocked
            && composer
                .chars()
                .next()
                .is_some_and(|ch| ch.is_ascii_digit()))
    {
        return None; // Native completion menus must remain fully visible and clickable.
    }
    // A fixed native status band prevents raw streaming tool rows from briefly
    // replacing typed conversation content or resizing its scroll viewport.
    Some(prompt.saturating_sub(area.y).saturating_sub(4))
}

#[cfg(test)]
mod tests;
