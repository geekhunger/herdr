use super::*;
use crossterm::event::{MouseButton, MouseEvent, MouseEventKind};

#[test]
fn image_preview_click_opens_panel_instead_of_selecting_or_forwarding_input() {
    let mut state = ClientShellState::new(ClientShellConfig::from_config(&Config::default()));
    let mut snapshot = snapshot();
    snapshot.agents.push(ClientShellAgent {
        pane_id: "pane_1".into(),
        workspace_id: "ws_1".into(),
        tab_id: "tab_1".into(),
        name: None,
        display_agent: None,
        agent: Some("codex".into()),
        title: None,
        terminal_title: None,
        terminal_title_stripped: Some("test conversation".into()),
        agent_status: AgentStatus::Idle,
        state_change_seq: 0,
        state_labels: Vec::new(),
        tokens: Vec::new(),
        focused: true,
    });
    state.set_snapshot(Box::new(snapshot));
    state.image_drafts.insert(
        "pane_1".into(),
        vec![crate::platform::ClipboardImage {
            bytes: vec![1, 2, 3],
            extension: "png",
        }],
    );
    state.observe_image_draft_key(
        &ClientInputTarget::Pane("pane_1".into()),
        &crate::input::TerminalKey::new(KeyCode::Enter, KeyModifiers::empty()),
    );
    assert!(
        !state.image_drafts.contains_key("pane_1"),
        "Sending a message must release its draft snapshots"
    );
    let mut surface = surface();
    let buffer = Buffer::with_lines(["› [Image #1] caption", "ordinary text here "]);
    surface.frame = FrameData::from_ratatui_buffer(&buffer, None);
    surface.panes[0].rect.width = buffer.area.width;
    surface.panes[0].inner_rect.width = buffer.area.width;
    state.set_pane_surface(surface);
    state.compose(106, 30).unwrap();
    let area = state.hits.panes[0].inner_rect;
    let mouse = |col| {
        RawInputEvent::Mouse(MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: area.x + col,
            row: area.y,
            modifiers: KeyModifiers::empty(),
        })
    };
    let result = state.handle_raw_events(vec![mouse(5)]);
    assert!(
        state.selection.is_none(),
        "Image badge clicks must not begin text selection"
    );
    assert!(
        result.actions.is_empty(),
        "Image badge clicks must not submit pane input"
    );
    state.handle_raw_events(vec![mouse(15)]);
    assert!(
        state.selection.is_some(),
        "Ordinary text must keep its selection behavior"
    );
}
