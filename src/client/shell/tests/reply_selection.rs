use super::*;
use crossterm::event::{MouseButton, MouseEventKind};

fn shell(copy_on_select: bool, codex: bool) -> ClientShellState {
    let mut config = Config::default();
    config.ui.copy_on_select = copy_on_select;
    let mut state = ClientShellState::new(ClientShellConfig::from_config(&config));
    let mut snapshot = snapshot();
    if codex {
        snapshot.agents.push(ClientShellAgent {
            pane_id: "pane_1".into(),
            workspace_id: "ws_1".into(),
            tab_id: "tab_1".into(),
            name: None,
            display_agent: None,
            agent: Some("Codex".into()),
            title: None,
            terminal_title: None,
            terminal_title_stripped: None,
            agent_status: AgentStatus::Idle,
            state_change_seq: 0,
            state_labels: Vec::new(),
            tokens: Vec::new(),
            focused: true,
        });
    }
    state.set_snapshot(Box::new(snapshot));
    state.set_pane_surface(surface());
    state.compose(106, 30).unwrap();
    state
}

fn drag(state: &mut ClientShellState) -> Vec<ClientShellAction> {
    let pane = state.hits.panes[0].inner_rect;
    let event = |kind, column| {
        RawInputEvent::Mouse(MouseEvent {
            kind,
            column,
            row: pane.y,
            modifiers: KeyModifiers::empty(),
        })
    };
    state.handle_raw_events(vec![event(MouseEventKind::Down(MouseButton::Left), pane.x)]);
    state.handle_raw_events(vec![event(
        MouseEventKind::Drag(MouseButton::Left),
        pane.x + 2,
    )]);
    state
        .handle_raw_events(vec![event(
            MouseEventKind::Up(MouseButton::Left),
            pane.x + 2,
        )])
        .actions
}

fn copy_reply(
    state: &mut ClientShellState,
    actions: &[ClientShellAction],
    text: &str,
) -> Vec<ClientShellAction> {
    let id = actions
        .iter()
        .find_map(|action| match action {
            ClientShellAction::Endpoint { request, .. }
                if matches!(
                    request.method,
                    crate::api::schema::Method::PaneSelectionRead(_)
                ) =>
            {
                Some(request.id.clone())
            }
            _ => None,
        })
        .unwrap();
    state
        .handle_endpoint_result(
            "boot-1",
            &id,
            Ok(crate::api::schema::ResponseResult::PaneSelection {
                pane_id: "pane_1".into(),
                text: text.into(),
            }),
        )
        .1
}

fn choose_reply(state: &mut ClientShellState) -> ClientShellInput {
    let pane = state.hits.panes[0].inner_rect;
    state.handle_raw_events(vec![RawInputEvent::Mouse(MouseEvent {
        kind: MouseEventKind::Down(MouseButton::Right),
        column: pane.x + 1,
        row: pane.y,
        modifiers: KeyModifiers::empty(),
    })]);
    let index = match state.overlay.as_ref().unwrap() {
        ClientShellOverlay::ContextMenu(menu) => menu
            .items()
            .iter()
            .position(|item| item.label == "Reply")
            .expect("Reply menu item"),
        _ => panic!("context menu"),
    };
    let mut outcome = ClientShellInput::default();
    state.activate_context_menu_item(index, &mut outcome);
    outcome
}

fn assert_draft(requests: &[ClientMessage]) {
    assert!(
        matches!(requests, [ClientMessage::ClientShellPaneInput { pane_id, events }]
        if pane_id == "pane_1" && matches!(events.as_slice(), [ClientPaneInputEvent::Paste(text)]
            if text == "> Erste Aussage\n> Zweite Aussage\n\n")),
        "quote is a single paste, without Enter"
    );
}

#[test]
fn drag_right_click_reply_pastes_quote_into_same_codex_without_submit() {
    let mut state = shell(true, true);
    state.set_endpoint_methods(Some(
        crate::server::client_commands::supported_client_shell_method_names()
            .iter()
            .map(|name| name.to_string())
            .collect(),
    ));
    let actions = drag(&mut state);
    assert!(
        state.selection.is_none(),
        "auto-copy already released selection"
    );
    let copied = copy_reply(&mut state, &actions, "Erste Aussage\nZweite Aussage");
    assert!(matches!(
        &copied[..],
        [ClientShellAction::ClipboardWrite(_)]
    ));
    let outcome = choose_reply(&mut state);
    assert_draft(&outcome.requests);
    assert!(outcome.actions.is_empty());
    assert!(state.overlay.is_none());
    assert!(state.reply_selection.is_none(), "consume quote only once");
}

#[test]
fn reply_waits_for_pending_copy_and_supports_manual_selection() {
    for auto_copy in [true, false] {
        let mut state = shell(auto_copy, true);
        let automatic = drag(&mut state);
        let outcome = choose_reply(&mut state);
        assert!(outcome.requests.is_empty());
        let read = if auto_copy {
            automatic
        } else {
            outcome.actions
        };
        copy_reply(&mut state, &read, "Erste Aussage\nZweite Aussage");
        let input = state.handle_raw_events(Vec::new());
        assert_draft(&input.requests);
    }
}

#[test]
fn reply_never_targets_shell_or_another_pane_and_drops_stale_copies() {
    let mut plain = shell(true, false);
    drag(&mut plain);
    assert!(!plain.can_reply_to_selection("pane_1"));
    let mut state = shell(true, true);
    let old = drag(&mut state);
    let latest = drag(&mut state);
    copy_reply(&mut state, &old, "stale");
    assert!(state.reply_selection.as_ref().unwrap().text.is_none());
    copy_reply(&mut state, &latest, "fresh");
    assert!(!state.can_reply_to_selection("pane_other"));
    let mut moved = snapshot();
    moved.focused_pane_id = Some("pane_other".into());
    state.set_snapshot(Box::new(moved));
    assert!(state.reply_selection.is_none());
}

#[test]
fn typing_after_selection_discards_reply_context() {
    let mut state = shell(true, true);
    let actions = drag(&mut state);
    copy_reply(&mut state, &actions, "old");
    state.handle_input_bytes(b"x");
    assert!(state.reply_selection.is_none());
}

#[test]
fn older_focus_snapshot_cannot_discard_a_newer_reply_selection() {
    let mut state = shell(true, true);
    let mut current = state.snapshot.as_deref().unwrap().clone();
    state.snapshot.as_mut().unwrap().revision = 20;
    let actions = drag(&mut state);
    copy_reply(&mut state, &actions, "Erste Aussage\nZweite Aussage");
    current.revision = 19;
    current.focused_pane_id = Some("older-pane".into());
    state.set_snapshot(Box::new(current));
    assert!(state.can_reply_to_selection("pane_1"));
    let outcome = choose_reply(&mut state);
    assert_draft(&outcome.requests);
}

fn mouse_reporting_shell(codex: bool) -> ClientShellState {
    let mut state = shell(true, codex);
    let mut frame = surface();
    frame.panes[0].mouse_reporting = true;
    state.set_pane_surface(frame);
    state.compose(106, 30).unwrap();
    state
}

#[test]
fn codex_mouse_reporting_drag_still_offers_reply() {
    let mut state = mouse_reporting_shell(true);
    let actions = drag(&mut state);
    copy_reply(&mut state, &actions, "Erste Aussage\nZweite Aussage");
    assert_draft(&choose_reply(&mut state).requests);
    assert!(state.pane_mouse_gesture.is_none());
}

#[test]
fn codex_single_click_and_modified_drag_still_reach_the_application() {
    let mut state = mouse_reporting_shell(true);
    let pane = state.hits.panes[0].inner_rect;
    let event = |kind, modifiers| {
        RawInputEvent::Mouse(MouseEvent {
            kind,
            column: pane.x + 1,
            row: pane.y,
            modifiers,
        })
    };
    let down = state.handle_raw_events(vec![event(
        MouseEventKind::Down(MouseButton::Left),
        KeyModifiers::empty(),
    )]);
    assert!(down.requests.is_empty(), "defer click until drag is known");
    let up = state.handle_raw_events(vec![event(
        MouseEventKind::Up(MouseButton::Left),
        KeyModifiers::empty(),
    )]);
    assert!(
        matches!(&up.requests[..],
        [ClientMessage::ClientShellPaneInput { events, .. }]
        if matches!(&events[..], [
            ClientPaneInputEvent::Mouse { kind: crate::protocol::ClientMouseKind::Down(_), .. },
            ClientPaneInputEvent::Mouse { kind: crate::protocol::ClientMouseKind::Up(_), .. }
        ])),
        "a normal click is delivered as a complete pair"
    );
    let down = state.handle_raw_events(vec![event(
        MouseEventKind::Down(MouseButton::Left),
        KeyModifiers::ALT,
    )]);
    assert!(!down.requests.is_empty());
    let drag = state.handle_raw_events(vec![event(
        MouseEventKind::Drag(MouseButton::Left),
        KeyModifiers::ALT,
    )]);
    assert!(!drag.requests.is_empty());
    assert!(state.selection.is_none());
}

#[test]
fn other_mouse_applications_keep_their_native_selection() {
    let mut state = mouse_reporting_shell(false);
    let pane = state.hits.panes[0].inner_rect;
    let down = state.handle_raw_events(vec![RawInputEvent::Mouse(MouseEvent {
        kind: MouseEventKind::Down(MouseButton::Left),
        column: pane.x,
        row: pane.y,
        modifiers: KeyModifiers::empty(),
    })]);
    assert!(!down.requests.is_empty());
    assert!(state.pane_mouse_gesture.is_some());
    assert!(state.selection.is_none());
}
