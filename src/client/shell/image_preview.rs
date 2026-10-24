//! Resolve image clicks only on demand, never during rendering or pane iteration.
use super::*;
use base64::Engine as _;
use crossterm::event::KeyModifiers;

fn marker_at(cells: &[crate::protocol::CellData], col: usize) -> Option<usize> {
    let text: String = cells.iter().map(|cell| cell.symbol.as_str()).collect();
    let byte_col: usize = cells.get(..col)?.iter().map(|cell| cell.symbol.len()).sum();
    let regex = regex::Regex::new(r"\[Image #(\d+)\]").ok()?;
    let result = regex.captures_iter(&text).find_map(|capture| {
        let whole = capture.get(0)?;
        (whole.start() <= byte_col && byte_col < whole.end())
            .then(|| capture.get(1)?.as_str().parse::<usize>().ok())
            .flatten()
            .filter(|index| *index > 0 && *index <= 100)
    });
    result
}

// The current composer begins at the last prompt before its cursor. Historical
// messages above that boundary must never receive a draft image by accident.
fn composer_images(
    frame: &crate::protocol::FrameData,
    rect: crate::protocol::SurfaceRect,
    clicked_row: usize,
) -> Option<usize> {
    let cursor = frame.cursor.as_ref()?;
    if cursor.y < rect.y || cursor.y >= rect.y.saturating_add(rect.height) {
        return None;
    }
    let width = usize::from(frame.width);
    let line = |row: usize| -> Option<String> {
        let start = row.checked_mul(width)? + usize::from(rect.x);
        Some(
            frame
                .cells
                .get(start..start + usize::from(rect.width))?
                .iter()
                .map(|c| c.symbol.as_str())
                .collect(),
        )
    };
    let bottom = usize::from(cursor.y);
    let top = (usize::from(rect.y)..=bottom)
        .rev()
        .find(|row| line(*row).is_some_and(|s| s.trim_start().starts_with('›')))?;
    if clicked_row < top || clicked_row > bottom {
        return None;
    }
    let regex = regex::Regex::new(r"\[Image #\d+\]").ok()?;
    Some(
        (top..=bottom)
            .filter_map(line)
            .map(|s| regex.find_iter(&s).count())
            .sum(),
    )
}

impl ClientShellState {
    pub(super) fn observe_image_draft_key(
        &mut self,
        target: &ClientInputTarget,
        key: &crate::input::TerminalKey,
    ) {
        let ClientInputTarget::Pane(pane_id) = target else {
            return;
        };
        if !self.active_endpoint_id.is_local() || !self.reply_pane_ready(pane_id) {
            return;
        }
        if key.code == KeyCode::Enter && key.modifiers.is_empty() {
            self.image_drafts.remove(pane_id);
        } else if key.code == KeyCode::Char('v') && key.modifiers == KeyModifiers::CONTROL {
            // Only an explicit image paste takes a clipboard snapshot. Draft images
            // stay in client memory and are discarded when the message is submitted.
            if let Some(image) = crate::platform::read_clipboard_image() {
                let cached_bytes: usize = self
                    .image_drafts
                    .values()
                    .flatten()
                    .map(|i| i.bytes.len())
                    .sum();
                if image.bytes.len() <= 24 * 1024 * 1024
                    && cached_bytes + image.bytes.len() <= 64 * 1024 * 1024
                {
                    self.image_drafts
                        .entry(pane_id.clone())
                        .or_default()
                        .push(image);
                }
            }
        }
    }

    pub(super) fn open_image_preview_at(
        &mut self,
        hit: &PaneHit,
        column: u16,
        row: u16,
        outcome: &mut ClientShellInput,
    ) -> bool {
        if !self.active_endpoint_id.is_local() || !contains(hit.inner_rect, (column, row)) {
            return false;
        }
        let Some(surface) = self.pane_surface.as_ref() else {
            return false;
        };
        let Some(pane) = surface
            .panes
            .iter()
            .find(|pane| pane.pane_id == hit.pane_id)
        else {
            return false;
        };
        let x = usize::from(pane.inner_rect.x);
        let y = usize::from(pane.inner_rect.y + row.saturating_sub(hit.inner_rect.y));
        let width = usize::from(surface.frame.width);
        let start = y * width + x;
        let Some(cells) = surface
            .frame
            .cells
            .get(start..start + usize::from(pane.inner_rect.width))
        else {
            return false;
        };
        let Some(index) = marker_at(cells, usize::from(column - hit.inner_rect.x)) else {
            return false;
        };
        let Some(snapshot) = self.snapshot.as_deref() else {
            return false;
        };
        let Some(agent) = snapshot.agents.iter().find(|agent| {
            agent.pane_id == hit.pane_id
                && agent
                    .agent
                    .as_deref()
                    .is_some_and(|a| a.eq_ignore_ascii_case("codex"))
        }) else {
            return false;
        };
        let cwd = snapshot
            .panes
            .iter()
            .find(|pane| pane.pane_id == hit.pane_id)
            .and_then(|pane| pane.foreground_cwd.as_ref().or(pane.cwd.as_ref()));
        let context: String = cells.iter().map(|cell| cell.symbol.as_str()).collect();
        let composer_count = composer_images(&surface.frame, pane.inner_rect, y);
        let draft_image = self
            .image_drafts
            .get(&hit.pane_id)
            .filter(|images| composer_count == Some(images.len()))
            .and_then(|images| images.get(index - 1))
            .map(|image| {
                format!(
                    "data:image/{};base64,{}",
                    image.extension,
                    base64::engine::general_purpose::STANDARD.encode(&image.bytes)
                )
            });
        let request = serde_json::json!({
            "draft_image": draft_image,
            "draft": composer_count.is_some(),
            "index": index, "context": context,
            "title": agent.terminal_title_stripped.as_ref().or(agent.title.as_ref()),
            "cwd": cwd, "pane": hit.pane_id,
        });
        match crate::platform::open_image_preview(&request.to_string()) {
            Ok(true) => {}
            Ok(false) => {
                self.set_endpoint_error("Image preview helper is not installed on this computer")
            }
            Err(error) => self.set_endpoint_error(format!("Image preview: {error}")),
        }
        self.selection = None;
        self.last_pane_click = None;
        outcome.repaint = true;
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn cells(text: &str) -> Vec<crate::protocol::CellData> {
        text.chars()
            .map(|c| crate::protocol::CellData {
                symbol: c.to_string(),
                fg: 0,
                bg: 0,
                modifier: 0,
                skip: false,
                hyperlink: None,
            })
            .collect()
    }
    #[test]
    fn image_preview_draft_boundary_does_not_capture_history() {
        let buffer = Buffer::with_lines([
            "› [Image #1] older ",
            "assistant reply    ",
            "› [Image #1] new   ",
            "  [Image #2]      ",
        ]);
        let frame = crate::protocol::FrameData::from_ratatui_buffer(
            &buffer,
            Some(crate::protocol::CursorState {
                x: 14,
                y: 3,
                visible: true,
                shape: 1,
            }),
        );
        let rect = crate::protocol::SurfaceRect {
            x: 0,
            y: 0,
            width: frame.width,
            height: frame.height,
        };
        assert_eq!(composer_images(&frame, rect, 0), None);
        assert_eq!(composer_images(&frame, rect, 2), Some(2));
        assert_eq!(composer_images(&frame, rect, 3), Some(2));
    }

    #[test]
    fn image_preview_marker_click_is_exact_and_unicode_safe() {
        let row = cells("› ä [Image #12] hello [Image #2]");
        assert_eq!(marker_at(&row, 4), Some(12));
        assert_eq!(marker_at(&row, 15), None);
        assert_eq!(marker_at(&row, 24), Some(2));
        assert_eq!(marker_at(&row, 2), None);
        assert_eq!(marker_at(&cells("[Image #0]"), 3), None);
    }
}
