//! Compact video references at the input boundary; paths remain in the local hook registry.
use super::*;

fn is_video(source: &str) -> bool {
    let path = source
        .split(['?', '#'])
        .next()
        .unwrap_or(source)
        .to_ascii_lowercase();
    [
        ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mpg", ".mpeg", ".m3u8",
    ]
    .iter()
    .any(|extension| path.ends_with(extension))
}

fn video_sources(text: &str) -> Option<Vec<String>> {
    let text = text.trim();
    if text.is_empty() || text.len() > 32 * 8192 {
        return None;
    }
    // Terminal file drags may quote paths or escape spaces. Do not reinterpret
    // mixed prose/code, ordinary text pastes, or other URL schemes.
    let token = regex::Regex::new(r#""(?:[^"\\]|\\.)*"|'[^']*'|(?:\\.|[^\s])+"#).ok()?;
    let tokens: Vec<&str> = token.find_iter(text).map(|m| m.as_str()).collect();
    let unquote = |s: &str| -> String {
        if s.len() >= 2
            && ((s.starts_with('"') && s.ends_with('"'))
                || (s.starts_with('\'') && s.ends_with('\'')))
        {
            s[1..s.len() - 1].to_string()
        } else {
            let mut value = String::new();
            let mut chars = s.chars();
            while let Some(ch) = chars.next() {
                if ch == '\\' {
                    if let Some(next) = chars.next() {
                        value.push(next);
                    }
                } else {
                    value.push(ch);
                }
            }
            value
        }
    };
    let plausible = |s: &str| {
        (s.starts_with('/')
            || s.starts_with("~/")
            || s.starts_with("./")
            || s.starts_with("file://")
            || s.starts_with("https://")
            || s.starts_with("http://"))
            && is_video(s)
    };
    let sources: Vec<String> = tokens.iter().map(|s| unquote(s)).collect();
    if !sources.is_empty() && sources.len() <= 32 && sources.iter().all(|s| plausible(s)) {
        return Some(sources);
    }
    // A single local path copied as plain text can contain unescaped spaces.
    if (text.starts_with('/') || text.starts_with("~/"))
        && !text.contains(['\n', '\r', '"', '\''])
        && is_video(text)
    {
        return Some(vec![text.to_string()]);
    }
    None
}

pub(super) fn video_marker_at(cells: &[crate::protocol::CellData], col: usize) -> Option<usize> {
    let text: String = cells.iter().map(|cell| cell.symbol.as_str()).collect();
    let byte_col: usize = cells.get(..col)?.iter().map(|cell| cell.symbol.len()).sum();
    let regex = regex::Regex::new(r"\[Video #(\d+)\]").ok()?;
    let result = regex.captures_iter(&text).find_map(|capture| {
        let whole = capture.get(0)?;
        (whole.start() <= byte_col && byte_col < whole.end())
            .then(|| capture.get(1)?.as_str().parse::<usize>().ok())
            .flatten()
            .filter(|n| *n > 0 && *n <= 1000)
    });
    result
}

impl ClientShellState {
    pub(super) fn compact_video_paste(&mut self, text: String) -> String {
        if !self.active_endpoint_id.is_local() {
            return text;
        }
        let Some(pane_id) = self
            .focused_pane_id()
            .filter(|id| self.reply_pane_ready(id))
        else {
            return text;
        };
        let Some(sources) = video_sources(&text) else {
            return text;
        };
        let cwd = self
            .snapshot
            .as_deref()
            .and_then(|s| s.panes.iter().find(|p| p.pane_id == pane_id))
            .and_then(|p| p.foreground_cwd.as_ref().or(p.cwd.as_ref()));
        let request = serde_json::json!({"sources": sources, "pane": pane_id, "socket": crate::api::socket_path(), "cwd": cwd});
        match crate::platform::register_video_attachments(&request.to_string()) {
            Ok(Some(compact)) => compact,
            Ok(None) => text,
            Err(error) => {
                self.set_endpoint_error(format!("Video attachment: {error}"));
                text
            }
        }
    }

    pub(super) fn open_video_preview_at(
        &mut self,
        hit: &PaneHit,
        column: u16,
        row: u16,
        outcome: &mut ClientShellInput,
    ) -> bool {
        if !self.active_endpoint_id.is_local() || !contains(hit.inner_rect, (column, row)) {
            return false;
        }
        let Some(snapshot) = self.snapshot.as_deref() else {
            return false;
        };
        if !snapshot.agents.iter().any(|a| {
            a.pane_id == hit.pane_id
                && a.agent
                    .as_deref()
                    .is_some_and(|a| a.eq_ignore_ascii_case("codex"))
        }) {
            return false;
        }
        let Some(surface) = self.pane_surface.as_ref() else {
            return false;
        };
        let Some(pane) = surface.panes.iter().find(|p| p.pane_id == hit.pane_id) else {
            return false;
        };
        let start = usize::from(pane.inner_rect.y + row - hit.inner_rect.y)
            * usize::from(surface.frame.width)
            + usize::from(pane.inner_rect.x);
        let Some(cells) = surface
            .frame
            .cells
            .get(start..start + usize::from(pane.inner_rect.width))
        else {
            return false;
        };
        let Some(index) = video_marker_at(cells, usize::from(column - hit.inner_rect.x)) else {
            return false;
        };
        let request = serde_json::json!({"media": "video", "index": index, "pane": hit.pane_id, "socket": crate::api::socket_path()});
        match crate::platform::open_image_preview(&request.to_string()) {
            Ok(true) => {}
            Ok(false) => self.set_endpoint_error("Video preview helper is not installed"),
            Err(error) => self.set_endpoint_error(format!("Video preview: {error}")),
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
    #[test]
    fn video_paste_handles_urls_and_terminal_file_paths_without_rewriting_prose() {
        assert_eq!(
            video_sources("file:///Users/me/My%20film.MOV"),
            Some(vec!["file:///Users/me/My%20film.MOV".into()])
        );
        assert_eq!(
            video_sources("'/Users/me/first film.mov' /tmp/second\\ film.mp4"),
            Some(vec![
                "/Users/me/first film.mov".into(),
                "/tmp/second film.mp4".into()
            ])
        );
        assert_eq!(
            video_sources("https://example.com/a.mp4?token=abc"),
            Some(vec!["https://example.com/a.mp4?token=abc".into()])
        );
        assert_eq!(
            video_sources("/Users/me/My film.mov"),
            Some(vec!["/Users/me/My film.mov".into()])
        );
        assert_eq!(
            video_sources("please analyze https://example.com/a.mp4"),
            None
        );
        assert_eq!(video_sources("https://example.com/watch?v=a"), None);
        assert_eq!(video_sources("/tmp/picture.png"), None);
    }
}
