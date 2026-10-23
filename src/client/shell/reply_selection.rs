//! Reply uses the selected text captured by the existing copy request, never the clipboard.
use super::*;

pub(super) struct ReplySelection {
    pub(super) endpoint_id: ClientEndpointId,
    pub(super) boot_id: String,
    pub(super) pane_id: String,
    pub(super) request_id: String,
    pub(super) text: Option<String>,
    pub(super) requested: bool,
}

fn quote(text: &str) -> Option<String> {
    if text.len() > 131_072 || text.trim().is_empty() {
        return None;
    }
    let normalized = text.replace("\r\n", "\n").replace('\r', "\n");
    let clean: String = normalized
        .chars()
        .filter(|c| !c.is_control() || *c == '\n' || *c == '\t')
        .collect();
    Some(
        clean
            .trim_end_matches('\n')
            .split('\n')
            .map(|line| format!("> {line}"))
            .collect::<Vec<_>>()
            .join("\n")
            + "\n\n",
    )
}

impl ClientShellState {
    pub(super) fn reply_pane_ready(&self, pane_id: &str) -> bool {
        self.snapshot.as_deref().is_some_and(|snapshot| {
            snapshot.focused_pane_id.as_deref() == Some(pane_id)
                && snapshot.agents.iter().any(|agent| {
                    agent.pane_id == pane_id
                        && agent
                            .agent
                            .as_deref()
                            .is_some_and(|name| name.eq_ignore_ascii_case("codex"))
                })
        }) && self.popup_terminal_id.is_none()
            && !self.popup_pending
    }

    fn cached_reply_matches(&self, pane_id: &str) -> bool {
        self.reply_selection.as_ref().is_some_and(|selection| {
            selection.pane_id == pane_id
                && selection.endpoint_id == self.active_endpoint_id
                && self
                    .snapshot
                    .as_deref()
                    .is_some_and(|snapshot| snapshot.boot_id == selection.boot_id)
        })
    }

    pub(super) fn can_reply_to_selection(&self, pane_id: &str) -> bool {
        self.reply_pane_ready(pane_id)
            && (self.cached_reply_matches(pane_id)
                || self.selection.as_ref().is_some_and(|selection| {
                    selection.pane_id == pane_id && selection.is_visible()
                }))
    }

    pub(super) fn begin_selection_reply(&mut self, pane_id: &str, outcome: &mut ClientShellInput) {
        if !self.can_reply_to_selection(pane_id) {
            return;
        }
        if !self.cached_reply_matches(pane_id) {
            self.request_selection_copy(outcome, true);
        }
        if let Some(selection) = self.reply_selection.as_mut() {
            selection.requested = true;
            if selection.text.is_none() {
                return;
            }
        }
        self.paste_selection_reply(outcome);
    }

    pub(super) fn capture_reply_copy(
        &mut self,
        request_id: &str,
        pane_id: &str,
        text: &str,
    ) -> Vec<ClientShellAction> {
        let matches = self.cached_reply_matches(pane_id)
            && self
                .reply_selection
                .as_ref()
                .is_some_and(|selection| selection.request_id == request_id);
        if !matches {
            return Vec::new();
        }
        let selection = self
            .reply_selection
            .as_mut()
            .expect("checked reply selection");
        if text.trim().is_empty() || text.len() > 131_072 {
            self.reply_selection = None;
            return Vec::new();
        }
        selection.text = Some(text.to_owned());
        if !selection.requested {
            return Vec::new();
        }
        let mut outcome = ClientShellInput::default();
        self.paste_selection_reply(&mut outcome);
        outcome.actions
    }

    fn paste_selection_reply(&mut self, outcome: &mut ClientShellInput) {
        let Some(selection) = self.reply_selection.take() else {
            return;
        };
        if !self.reply_pane_ready(&selection.pane_id)
            || selection.endpoint_id != self.active_endpoint_id
            || self
                .snapshot
                .as_deref()
                .is_none_or(|snapshot| snapshot.boot_id != selection.boot_id)
        {
            return;
        }
        let Some(text) = selection.text.as_deref().and_then(quote) else {
            return;
        };
        self.selection = None;
        self.selection_highlight_clear_deadline = None;
        self.word_selection_gesture = None;
        if self.mode == ClientShellMode::Copy {
            self.exit_copy_mode(false, outcome);
        }
        // Existing endpoint API encodes text as a paste. No Enter or submission is sent.
        self.push_endpoint_method(
            crate::api::schema::Method::PaneSendInput(crate::api::schema::PaneSendInputParams {
                pane_id: selection.pane_id,
                text,
                keys: Vec::new(),
            }),
            outcome,
        );
        outcome.repaint = true;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn quote_preserves_lines_and_unicode_without_terminal_controls() {
        assert_eq!(
            quote("Äpfel\r\nzweite Zeile\n").as_deref(),
            Some("> Äpfel\n> zweite Zeile\n\n")
        );
        assert_eq!(quote("safe\x1b\x00text").as_deref(), Some("> safetext\n\n"));
        assert!(quote(" \n\t").is_none());
        assert!(quote(&"x".repeat(131_073)).is_none());
    }
}
