//! One explicit Ctrl+L request, using Codex's temporary-draft editor interface.
use super::*;
use crossterm::event::{KeyEventKind, KeyModifiers};
use serde::Deserialize;
use sha2::{Digest, Sha256};

#[derive(Deserialize)]
struct Ready {
    pid: u32,
    token: String,
}

impl ClientShellState {
    pub(super) fn handle_clear_draft(
        &mut self,
        key: &crate::input::TerminalKey,
        outcome: &mut ClientShellInput,
    ) -> bool {
        if key.code != KeyCode::Char('l')
            || key.modifiers != KeyModifiers::CONTROL
            || self.mode != ClientShellMode::Terminal
            || self.overlay.is_some()
        {
            return false;
        }
        let Some(pane_id) = self
            .focused_pane_id()
            .filter(|id| self.reply_pane_ready(id))
        else {
            return false;
        };
        if key.kind != KeyEventKind::Press {
            return true;
        }
        if !self.active_endpoint_id.is_local() {
            self.set_endpoint_error("Ctrl+L benötigt den lokalen Codex-Editorhelfer");
            outcome.repaint = true;
            return true;
        }
        // Filesystem work happens only on this explicit shortcut, never during
        // rendering, pane iteration, or ordinary typing.
        let socket = crate::api::socket_path();
        let digest = Sha256::digest(format!("{}\0{pane_id}", socket.to_string_lossy()).as_bytes());
        let root = crate::config::config_dir().join("draft-editor");
        let ready = root.join(format!("{digest:x}.ready"));
        let result = (|| -> Result<(), String> {
            if std::fs::metadata(&ready).map_err(|e| e.to_string())?.len() > 4096 {
                return Err("Ungültiger Editorhelfer-Status".into());
            }
            let data = std::fs::read(&ready).map_err(|e| e.to_string())?;
            let ready: Ready = serde_json::from_slice(&data).map_err(|e| e.to_string())?;
            if ready.token.len() != 32
                || !ready.token.bytes().all(|b| b.is_ascii_hexdigit())
                || !crate::platform::process_exists(ready.pid)
            {
                return Err("Editorhelfer ist nicht aktiv".into());
            }
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_err(|e| e.to_string())?
                .as_millis();
            let request = serde_json::json!({"token": ready.token, "created_ms": now});
            std::fs::write(
                root.join(format!("{digest:x}.request")),
                request.to_string(),
            )
            .map_err(|e| e.to_string())?;
            Ok(())
        })();
        if result.is_err() {
            self.set_endpoint_error(
                "Ctrl+L: Codex einmal mit dem aktualisierten Herdr-Profil neu starten",
            );
            outcome.repaint = true;
            return true;
        }
        self.reply_selection = None;
        self.pending_reply_paste = None;
        self.selection = None;
        self.word_selection_gesture = None;
        self.selection_highlight_clear_deadline = None;
        self.stop_selection_autoscroll();
        if let Some(event) = ClientPaneInputEvent::from_terminal_key(
            crate::input::TerminalKey::new(KeyCode::Char('g'), KeyModifiers::CONTROL),
        ) {
            push_target_event(ClientInputTarget::Pane(pane_id), event, outcome);
        }
        outcome.repaint = true;
        true
    }
}
