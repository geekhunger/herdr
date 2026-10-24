use super::*;
use crate::protocol::{ClientShellAgent, CursorState, PaneSurfaceScrollMetrics};
use crate::raw_input::RawInputEvent;

fn document() -> Document {
    Document {
        session_id: "session-1".into(),
        revision: 1,
        blocks: vec![
            Block {
                id: "user".into(),
                kind: "user".into(),
                text: "My important message".into(),
                summary: String::new(),
                output: String::new(),
                pending: false,
            },
            Block {
                id: "tool-1".into(),
                kind: "tool".into(),
                text: "cat file.txt".into(),
                summary: "exec · cat file.txt".into(),
                output: "CONSOLE OUTPUT ONE".into(),
                pending: false,
            },
            Block {
                id: "tool-2".into(),
                kind: "tool".into(),
                text: "pwd".into(),
                summary: "exec · pwd".into(),
                output: "CONSOLE OUTPUT TWO".into(),
                pending: true,
            },
            Block {
                id: "answer".into(),
                kind: "assistant".into(),
                text: "The important answer".into(),
                summary: String::new(),
                output: String::new(),
                pending: false,
            },
        ],
    }
}

fn shell() -> ClientShellState {
    let config: Config = toml::from_str(
        "[ui]\ncopy_on_select=false\n[keys]\nprefix=\"ctrl+f\"\ncopy_mode=\"prefix+y\"\ncopy_search=\"prefix+f\"\ngoto=\"prefix+ctrl+f\"",
    )
    .unwrap();
    let mut state = ClientShellState::new(ClientShellConfig::from_config(&config));
    let mut snapshot = super::super::tests::snapshot();
    snapshot.agents.push(ClientShellAgent {
        pane_id: "pane_1".into(),
        workspace_id: "ws_1".into(),
        tab_id: "tab_1".into(),
        name: None,
        display_agent: None,
        agent: Some("codex".into()),
        title: None,
        terminal_title: None,
        terminal_title_stripped: Some("Test | herrsch".into()),
        agent_status: crate::api::schema::AgentStatus::Working,
        state_change_seq: 1,
        state_labels: Vec::new(),
        tokens: Vec::new(),
        focused: true,
    });
    state.set_snapshot(Box::new(snapshot));
    let mut surface = super::super::tests::surface();
    let mut buffer = Buffer::empty(Rect::new(0, 0, 60, 22));
    buffer.set_string(0, 0, "native console output", Style::default());
    buffer.set_string(0, 16, "• Working (1s · esc to interrupt)", Style::default());
    buffer.set_string(0, 19, "› unsent draft", Style::default());
    surface.frame = FrameData::from_ratatui_buffer(
        &buffer,
        Some(CursorState {
            x: 14,
            y: 19,
            visible: true,
            shape: 1,
        }),
    );
    surface.panes[0].rect = crate::protocol::SurfaceRect {
        x: 0,
        y: 0,
        width: 60,
        height: 22,
    };
    surface.panes[0].inner_rect = surface.panes[0].rect;
    surface.panes[0].scroll = Some(PaneSurfaceScrollMetrics {
        offset_from_bottom: 0,
        max_offset_from_bottom: 0,
        viewport_rows: 22,
    });
    state.set_pane_surface(surface);
    state.transcript.target = Some(Target {
        pane_id: "pane_1".into(),
        boot_id: "boot-1".into(),
        title: "Test | herrsch".into(),
        cwd: "/repo".into(),
    });
    state
        .transcript
        .view_targets
        .insert("pane_1".into(), state.transcript.target.clone().unwrap());
    state
        .transcript
        .views
        .insert("pane_1".into(), View::new(document()));
    state.compose(106, 24).expect("frame");
    state
}

fn text(state: &mut ClientShellState) -> String {
    let frame = state.compose(106, 24).unwrap();
    frame
        .cells
        .iter()
        .map(|cell| cell.symbol.as_str())
        .collect()
}

#[test]
fn default_folding_keeps_messages_answers_composer_and_status() {
    let mut state = shell();
    let text = text(&mut state);
    for expected in [
        "My important message",
        "The important answer",
        "unsent draft",
        "Working",
        "▸ exec",
    ] {
        assert!(text.contains(expected), "{expected}");
    }
    assert!(!text.contains("CONSOLE OUTPUT"));
    assert!(!text.contains("native console output"));
    assert!(state.compose(106, 24).unwrap().cursor.is_some());
}

#[test]
fn native_surface_patches_cannot_overwrite_folded_rows_or_their_mouse_targets() {
    let mut state = shell();
    let mut next_revision = state.pane_surface.as_ref().unwrap().surface_revision;
    for _ in 0..4 {
        let surface = state.pane_surface.as_ref().unwrap();
        let mut pane = surface.panes[0].clone();
        pane.content_revision += 1;
        let native = Buffer::with_lines(["NEW NATIVE OUTPUT THAT MUST NOT REPLACE FOLDED CONTENT"]);
        let native = FrameData::from_ratatui_buffer(&native, None);
        let patch = crate::protocol::PaneSurfacePatch {
            boot_id: surface.boot_id.clone(),
            projection_revision: surface.projection_revision,
            base_surface_revision: next_revision,
            surface_revision: next_revision + 1,
            cursor: surface.frame.cursor.clone(),
            panes: vec![pane],
            rows: vec![crate::protocol::PaneSurfacePatchRow {
                x: 0,
                y: 0,
                cells: native.cells,
            }],
        };
        assert!(
            matches!(
                state.apply_pane_surface_patch(patch),
                ClientPaneSurfacePatchOutcome::Applied(None)
            ),
            "transcript must recompose instead of leaking raw terminal patches"
        );
        next_revision += 1;
        let frame = text(&mut state);
        assert!(!frame.contains("NEW NATIVE OUTPUT"));
        assert!(frame.contains("Show details"));
        let view = &state.transcript.views["pane_1"];
        let index = view
            .rows
            .iter()
            .position(|row| row.kind == RowKind::Tool)
            .unwrap();
        let row = state.transcript.body.y + (index - view.top) as u16;
        // Exercise exactly the two controls the user clicks, through raw mouse bytes.
        let col = if next_revision.is_multiple_of(2) {
            state.transcript.body.x + 1
        } else {
            state.transcript.body.right() - 8
        };
        state.handle_input_bytes(format!("\x1b[<0;{};{}M", col + 1, row + 1).as_bytes());
        state.handle_input_bytes(format!("\x1b[<0;{};{}m", col + 1, row + 1).as_bytes());
        assert!(text(&mut state).contains("CONSOLE OUTPUT ONE"));
        state.handle_input_bytes(b"\x14");
        state.handle_input_bytes(b"\x14");
        assert!(!text(&mut state).contains("CONSOLE OUTPUT ONE"));
    }
}

#[test]
fn mouse_wheel_scrolls_the_conversation_and_native_updates_keep_the_reading_position() {
    let mut state = shell();
    {
        let view = state.transcript.views.get_mut("pane_1").unwrap();
        view.document.blocks[0].text = (0..60).map(|n| format!("Message line {n}\n")).collect();
        view.dirty = true;
    }
    state.compose(106, 24).unwrap();
    let area = state.transcript.body;
    let wheel = format!("\x1b[<64;{};{}M", area.x + 6, area.y + 3);
    let outcome = state.handle_input_bytes(wheel.as_bytes());
    assert!(outcome.requests.is_empty() && outcome.repaint);
    state.compose(106, 24).unwrap();
    assert_eq!(state.transcript.views["pane_1"].offset, 3);
    let wheel = format!("\x1b[<65;{};{}M", area.x + 6, area.y + 3);
    state.handle_input_bytes(wheel.as_bytes());
    state.compose(106, 24).unwrap();
    assert_eq!(state.transcript.views["pane_1"].offset, 0);
}

#[test]
fn native_background_and_markdown_survive_without_internal_citations() {
    use ratatui::style::Color;
    let mut state = shell();
    let native_bg = Color::Rgb(34, 40, 53);
    let native_fg = Color::Rgb(230, 234, 241);
    state.config.palette.panel_bg = Color::Black;
    let surface = state.pane_surface.as_mut().unwrap();
    let mut source = surface.frame.to_ratatui_buffer().unwrap();
    source.set_style(
        source.area,
        Style::default()
            .fg(native_fg)
            .bg(native_bg)
            .add_modifier(Modifier::BOLD),
    );
    source.set_style(
        Rect::new(0, 0, 60, 16),
        Style::default().bg(Color::Rgb(80, 20, 20)),
    );
    let cursor = surface.frame.cursor.clone();
    surface
        .frame
        .replace_from_ratatui_buffer_preserving_effects(&source, cursor);
    let view = state.transcript.views.get_mut("pane_1").unwrap();
    view.document.blocks.last_mut().unwrap().text = "An **important** answer with `Ctrl+T`.\n<oai-mem-citation>\n<citation_entries>\nMEMORY.md:1-2|note=[internal]\n</citation_entries>\n</oai-mem-citation>".into();
    view.dirty = true;
    let frame = state.compose(106, 24).unwrap();
    let text: String = frame
        .cells
        .iter()
        .map(|cell| cell.symbol.as_str())
        .collect();
    assert!(text.contains("An important answer with Ctrl+T."));
    for hidden in [
        "MEMORY.md",
        "oai-mem",
        "citation_entries",
        "**important**",
        "`Ctrl+T`",
    ] {
        assert!(!text.contains(hidden), "{hidden}");
    }
    let buffer = frame.to_ratatui_buffer().unwrap();
    let view = &state.transcript.views["pane_1"];
    let row_index = view
        .rows
        .iter()
        .position(|row| row.text.contains("An important"))
        .unwrap();
    let row = &view.rows[row_index];
    let y = state.transcript.body.y + (row_index - view.top) as u16;
    let x = state.transcript.body.x;
    let important = row.text.find("important").unwrap() as u16;
    assert!(buffer[(x + important, y)].modifier.contains(Modifier::BOLD));
    assert!(
        !buffer[(x + 2, y)].modifier.contains(Modifier::BOLD),
        "native cell modifiers must not bleed into plain prose"
    );
    assert_eq!(buffer[(x + 2, y)].bg, native_bg);
    assert_eq!(buffer[(x + 2, y)].fg, native_fg);
    let code = row.text.find("Ctrl+T").unwrap() as u16;
    assert_eq!(buffer[(x + code, y)].fg, Color::Magenta);
    assert_eq!(buffer[(x + code, y)].bg, native_bg);
}

#[test]
fn completed_markdown_list_is_not_repeated_as_a_streaming_tail() {
    let mut state = shell();
    let view = state.transcript.views.get_mut("pane_1").unwrap();
    view.document.blocks.last_mut().unwrap().text =
        "Done.\n\n- A completed answer with **bold** text.\n- Last completed bullet with `code`."
            .into();
    view.dirty = true;
    let surface = state.pane_surface.as_mut().unwrap();
    let mut source = surface.frame.to_ratatui_buffer().unwrap();
    source.set_string(
        0,
        12,
        "• Last completed bullet with code.",
        Style::default(),
    );
    let cursor = surface.frame.cursor.clone();
    surface
        .frame
        .replace_from_ratatui_buffer_preserving_effects(&source, cursor);
    state.compose(106, 24).unwrap();
    assert_eq!(
        state.transcript.area.height, 15,
        "the completed native tail should be covered, not duplicated"
    );
}

#[test]
fn markdown_ranges_follow_wrapping_and_keep_code_literal() {
    let rows = markup::wrap("**Äpfel** and `a*b`\n```text\n**literal**\n```", 9);
    assert_eq!(rows[0].text, "Äpfel and");
    assert!(rows[0]
        .decorations
        .iter()
        .any(|decoration| decoration.start == 0
            && decoration.end == 5
            && decoration.modifiers.contains(Modifier::BOLD)));
    let plain = rows
        .iter()
        .map(|row| row.text.as_str())
        .collect::<Vec<_>>()
        .join("");
    assert!(plain.contains("a*b"));
    assert!(plain.contains("**literal**"));
    assert!(!plain.contains("```"));
}

#[test]
fn ctrl_t_toggles_all_and_single_click_only_one_block() {
    let mut state = shell();
    let toggle = state.handle_input_bytes(b"\x14");
    assert!(toggle.requests.is_empty() && toggle.actions.is_empty());
    let expanded = text(&mut state);
    assert!(expanded.contains("CONSOLE OUTPUT ONE") && expanded.contains("CONSOLE OUTPUT TWO"));
    state.handle_input_bytes(b"\x14");
    assert!(!text(&mut state).contains("CONSOLE OUTPUT"));
    let view = &state.transcript.views["pane_1"];
    let row = view
        .rows
        .iter()
        .position(|row| row.kind == RowKind::Tool)
        .unwrap();
    let y = state.transcript.body.y + (row - view.top) as u16;
    let x = state.transcript.body.x + 2;
    let result = state.handle_raw_events(vec![RawInputEvent::Mouse(MouseEvent {
        kind: MouseEventKind::Down(MouseButton::Left),
        column: x,
        row: y,
        modifiers: KeyModifiers::empty(),
    })]);
    assert!(result.requests.is_empty());
    let individually = text(&mut state);
    assert!(individually.contains("CONSOLE OUTPUT ONE"));
    assert!(!individually.contains("CONSOLE OUTPUT TWO"));
    state.handle_input_bytes(b"\x14");
    assert!(text(&mut state).contains("CONSOLE OUTPUT TWO"));
}

#[test]
fn direct_search_uses_full_conversation_and_approval_is_never_hidden() {
    let mut state = shell();
    state.handle_input_bytes(b"\x06f");
    assert_eq!(state.mode, ClientShellMode::Copy);
    state.compose(106, 24).unwrap();
    assert!(!state.transcript.area.is_empty());
    assert!(state.transcript.copy.is_some());
    state.handle_input_bytes(b"\x1b");
    state.handle_input_bytes(b"\x1b");
    state.snapshot.as_mut().unwrap().agents[0].agent_status =
        crate::api::schema::AgentStatus::Blocked;
    assert!(!text(&mut state).contains("native console output"));
    assert!(
        !state.transcript.area.is_empty(),
        "a status change is not an approval dialog"
    );
    let surface = state.pane_surface.as_mut().unwrap();
    let mut buffer = surface.frame.to_ratatui_buffer().unwrap();
    buffer.set_string(
        0,
        17,
        "Would you like to run this command?",
        Style::default(),
    );
    buffer.set_string(
        0,
        19,
        "› 1. Yes, proceed                 ",
        Style::default(),
    );
    let cursor = surface.frame.cursor.clone();
    surface
        .frame
        .replace_from_ratatui_buffer_preserving_effects(&buffer, cursor);
    assert!(text(&mut state).contains("Would you like to run this command?"));
    assert!(state.transcript.area.is_empty());
}

fn long_chat() -> ClientShellState {
    let mut state = shell();
    let view = state.transcript.views.get_mut("pane_1").unwrap();
    view.document.blocks[0].text = (0..150).map(|n| format!("History line {n:03}\n")).collect();
    view.dirty = true;
    state.compose(106, 24).unwrap();
    state
}

#[test]
fn prefix_keeps_folding_and_copy_arrows_continue_past_both_viewport_edges() {
    let mut state = long_chat();
    state.handle_input_bytes(b"\x06");
    assert!(text(&mut state).contains("Show details"));
    assert!(!text(&mut state).contains("native console output"));
    state.handle_input_bytes(b"y");
    assert!(state.transcript.copy.is_some());
    for _ in 0..200 {
        let outcome = state.handle_input_bytes(b"\x1b[A");
        assert!(outcome.actions.is_empty() && outcome.requests.is_empty());
        state.compose(106, 24).unwrap();
    }
    assert_eq!(state.transcript.copy.as_ref().unwrap().row, 0);
    assert_eq!(state.transcript.views["pane_1"].top, 0);
    assert!(text(&mut state).contains("History line 000"));
    for _ in 0..80 {
        state.handle_input_bytes(b"\x1b[B");
        state.compose(106, 24).unwrap();
    }
    let cursor = state.transcript.copy.as_ref().unwrap();
    let view = &state.transcript.views["pane_1"];
    assert_eq!(cursor.row, 80);
    assert!(view.top > 50);
    assert!(
        cursor.row >= view.top && cursor.row < view.top + usize::from(state.transcript.body.height)
    );
    let view = state.transcript.views.get_mut("pane_1").unwrap();
    let mut next = view.document.clone();
    next.revision += 1;
    next.blocks
        .last_mut()
        .unwrap()
        .text
        .push_str("\nNewest answer after copy");
    view.pending_document = Some(next);
    state.handle_input_bytes(b"\x1b");
    assert_eq!(state.mode, ClientShellMode::Terminal);
    assert!(state.transcript.copy.is_none());
    let rendered = text(&mut state);
    assert_eq!(state.transcript.views["pane_1"].offset, 0);
    assert!(rendered.contains("Newest answer after copy"));
    assert!(!rendered.contains("native console output"));
}

#[test]
fn full_history_search_preserves_fixed_k_l_directions_and_copies_visible_match() {
    let mut state = long_chat();
    state.handle_input_bytes(b"\x06");
    state.compose(106, 24).unwrap();
    state.handle_input_bytes(b"f");
    state.handle_input_bytes(b"History line 0");
    let result = state.handle_input_bytes(b"\r");
    assert!(result.actions.is_empty() && result.requests.is_empty());
    state.compose(106, 24).unwrap();
    assert_eq!(state.transcript.copy.as_ref().unwrap().row, 99);
    for (key, row) in [(b"k", 0), (b"L", 1), (b"l", 0), (b"K", 99)] {
        state.handle_input_bytes(key);
        state.compose(106, 24).unwrap();
        assert_eq!(state.transcript.copy.as_ref().unwrap().row, row);
    }
    let copied = state.handle_input_bytes(b"y");
    assert!(copied.requests.is_empty());
    assert!(
        matches!(&copied.actions[..], [ClientShellAction::ClipboardWrite(bytes)] if bytes == b"History line 0")
    );
}

#[test]
fn new_responses_and_streaming_height_changes_do_not_move_a_scrolled_reading_position() {
    let mut state = long_chat();
    let area = state.transcript.body;
    state.handle_input_bytes(format!("\x1b[<64;{};{}M", area.x + 5, area.y + 5).as_bytes());
    state.compose(106, 24).unwrap();
    let top = state.transcript.views["pane_1"].top;
    let surface = state.pane_surface.as_mut().unwrap();
    let mut source = surface.frame.to_ratatui_buffer().unwrap();
    source.set_string(0, 10, "• A fresh streaming response", Style::default());
    let cursor = surface.frame.cursor.clone();
    surface
        .frame
        .replace_from_ratatui_buffer_preserving_effects(&source, cursor);
    state.compose(106, 24).unwrap();
    assert_eq!(state.transcript.views["pane_1"].top, top);
    state.handle_input_bytes(b"\x06y");
    state.compose(106, 24).unwrap();
    let copy_row = state.transcript.copy.as_ref().unwrap().row;
    let view = state.transcript.views.get_mut("pane_1").unwrap();
    let mut next = view.document.clone();
    next.revision += 1;
    next.blocks
        .last_mut()
        .unwrap()
        .text
        .push_str("\nNew answer");
    view.pending_document = Some(next);
    state.compose(106, 24).unwrap();
    assert_eq!(state.transcript.copy.as_ref().unwrap().row, copy_row);
    state.handle_input_bytes(b"q");
    assert!(state.transcript.views["pane_1"]
        .document
        .blocks
        .last()
        .unwrap()
        .text
        .contains("New answer"));
}

#[test]
fn selecting_and_reply_use_visible_transcript_text_without_terminal_copy_requests() {
    let mut state = shell();
    let body = state.transcript.body;
    let event = |kind, x| {
        RawInputEvent::Mouse(MouseEvent {
            kind,
            column: x,
            row: body.y,
            modifiers: KeyModifiers::empty(),
        })
    };
    state.handle_raw_events(vec![event(
        MouseEventKind::Down(MouseButton::Left),
        body.x + 2,
    )]);
    state.handle_raw_events(vec![event(
        MouseEventKind::Drag(MouseButton::Left),
        body.x + 10,
    )]);
    let released = state.handle_raw_events(vec![event(
        MouseEventKind::Up(MouseButton::Left),
        body.x + 10,
    )]);
    assert!(released.actions.is_empty() && released.requests.is_empty());
    assert!(state.can_reply_to_selection("pane_1"));
    assert_eq!(
        state.reply_selection.as_ref().unwrap().text.as_deref(),
        Some("My import")
    );
    let copied = state.handle_input_bytes(b"\x03");
    assert!(copied.requests.is_empty());
    assert!(
        matches!(&copied.actions[..], [ClientShellAction::ClipboardWrite(bytes)] if bytes == b"My import")
    );
    let mut reply = ClientShellInput::default();
    state.begin_selection_reply("pane_1", &mut reply);
    assert!(
        matches!(&reply.requests[..], [ClientMessage::ClientShellPaneInput { events, .. }] if matches!(&events[..], [ClientPaneInputEvent::Paste(text)] if text == "> My import\n"))
    );
}

#[test]
fn late_output_inherits_fold_mode_and_unicode_wrapping_is_cell_aware() {
    let mut view = View::new(document());
    view.toggle_all();
    let mut next = document();
    next.revision += 1;
    next.blocks.push(Block {
        id: "new".into(),
        kind: "tool".into(),
        text: "new command".into(),
        summary: "new".into(),
        output: "LATE OUTPUT".into(),
        pending: false,
    });
    view.update(next);
    view.prepare(20);
    assert!(view.rows.iter().any(|row| row.text.contains("LATE OUTPUT")));
    view.toggle_all();
    view.prepare(20);
    assert!(!view.rows.iter().any(|row| row.text.contains("LATE OUTPUT")));
    assert_eq!(view::wrap("Ä界e\u{301}", 3), vec!["Ä界", "e\u{301}"]);
}

#[test]
fn raw_streaming_tail_cannot_replace_compact_history_and_identity_is_preserved() {
    let mut state = shell();
    let surface = state.pane_surface.as_mut().unwrap();
    let mut buffer = surface.frame.to_ratatui_buffer().unwrap();
    buffer.set_string(0, 12, "• A currently streaming answer", Style::default());
    let cursor = surface.frame.cursor.clone();
    surface
        .frame
        .replace_from_ratatui_buffer_preserving_effects(&buffer, cursor);
    assert!(!text(&mut state).contains("A currently streaming answer"));
    assert_eq!(state.transcript.area.height, 15);
    state.snapshot.as_mut().unwrap().boot_id = "different-server".into();
    assert!(!state.transcript_visible());
    state.snapshot.as_mut().unwrap().boot_id = "boot-1".into();
    state.snapshot.as_mut().unwrap().agents[0].terminal_title_stripped =
        Some("A different conversation | herrsch".into());
    assert!(!state.transcript_visible());
}

#[test]
fn scrolling_stays_on_the_same_message_when_new_output_arrives() {
    let mut state = shell();
    {
        let view = state.transcript.views.get_mut("pane_1").unwrap();
        view.document.blocks[0].text = (0..45).map(|n| format!("Important line {n}\n")).collect();
        view.dirty = true;
    }
    state.compose(106, 24).unwrap();
    state.handle_input_bytes(b"\x1b[5~");
    state.compose(106, 24).unwrap();
    let before = {
        let view = &state.transcript.views["pane_1"];
        assert!(view.offset > 0);
        view.rows[view.top].text.clone()
    };
    {
        let view = state.transcript.views.get_mut("pane_1").unwrap();
        let mut next = view.document.clone();
        next.revision += 1;
        next.blocks
            .last_mut()
            .unwrap()
            .text
            .push_str("\nNew output\nMore output");
        view.update(next);
    }
    state.compose(106, 24).unwrap();
    let view = &state.transcript.views["pane_1"];
    assert_eq!(view.rows[view.top].text, before);
}

#[test]
fn image_badges_resolve_from_their_own_message_without_changing_drafts() {
    let mut state = shell();
    let view = state.transcript.views.get_mut("pane_1").unwrap();
    view.document.blocks[0].text = "Ä [Image #1] my image".into();
    view.dirty = true;
    state.compose(106, 24).unwrap();
    let area = state.transcript.body;
    let request = state
        .transcript
        .attachment_request(MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: area.x + 8,
            row: area.y,
            modifiers: KeyModifiers::empty(),
        })
        .unwrap();
    assert_eq!(request["index"], 1);
    assert_eq!(request["context"], "Ä [Image #1] my image");
    assert!(request.get("draft_image").is_none());
}

#[test]
fn native_slash_command_completion_is_not_covered() {
    let mut state = shell();
    let surface = state.pane_surface.as_mut().unwrap();
    let mut buffer = surface.frame.to_ratatui_buffer().unwrap();
    buffer.set_string(0, 19, "› /model             ", Style::default());
    let cursor = surface.frame.cursor.clone();
    surface
        .frame
        .replace_from_ratatui_buffer_preserving_effects(&buffer, cursor);
    assert!(text(&mut state).contains("native console output"));
    assert!(state.transcript.area.is_empty());
}

#[test]
#[ignore = "manual focused transcript render scaling profile"]
fn transcript_render_scale_profile() {
    for panes in [1, 15] {
        for enabled in [false, true] {
            let mut state = shell();
            let snapshot = state.snapshot.as_mut().unwrap();
            for index in 1..panes {
                let mut pane = snapshot.panes[0].clone();
                pane.pane_id = format!("background_{index}");
                pane.focused = false;
                snapshot.panes.push(pane);
                let mut agent = snapshot.agents[0].clone();
                agent.pane_id = format!("background_{index}");
                agent.focused = false;
                snapshot.agents.push(agent);
                let surface = state.pane_surface.as_mut().unwrap();
                let mut pane = surface.panes[0].clone();
                pane.pane_id = format!("background_{index}");
                pane.focused = false;
                surface.panes.push(pane);
            }
            if !enabled {
                state.transcript.target = None;
            }
            for _ in 0..20 {
                std::hint::black_box(state.compose(106, 24).unwrap());
            }
            let start = Instant::now();
            for _ in 0..500 {
                std::hint::black_box(state.compose(106, 24).unwrap());
            }
            eprintln!(
                "transcript: panes={panes}, enabled={enabled}, {:.1} us/frame",
                start.elapsed().as_secs_f64() * 2000.0
            );
        }
    }
}

#[test]
fn code_highlighting_keeps_multiple_token_colors_after_wrapping() {
    use ratatui::style::Color;
    let rows = markup::wrap(
        "```rust\nlet größe = calculate(42, \"hello\"); // comment\n```",
        18,
    );
    let colors: Vec<_> = rows
        .iter()
        .flat_map(|row| row.decorations.iter().filter_map(|span| span.fg))
        .collect();
    for color in [
        Color::Magenta,
        Color::Blue,
        Color::Yellow,
        Color::Green,
        Color::DarkGray,
    ] {
        assert!(colors.contains(&color), "missing {color:?}");
    }
    assert_eq!(
        rows.iter().map(|row| row.text.as_str()).collect::<String>(),
        "let größe = calculate(42, \"hello\"); // comment"
    );
    let mut state = shell();
    state
        .transcript
        .views
        .get_mut("pane_1")
        .unwrap()
        .document
        .blocks
        .last_mut()
        .unwrap()
        .text = "```rust\nlet value = calculate(42, \"hello\");\n```".into();
    state.transcript.views.get_mut("pane_1").unwrap().dirty = true;
    let frame = state.compose(106, 24).unwrap();
    let buffer = frame.to_ratatui_buffer().unwrap();
    let view = &state.transcript.views["pane_1"];
    let index = view
        .rows
        .iter()
        .position(|row| row.text.contains("let value"))
        .unwrap();
    let y = state.transcript.body.y + (index - view.top) as u16;
    assert_eq!(buffer[(state.transcript.body.x + 2, y)].fg, Color::Magenta);
    state.handle_input_bytes(b"\x06");
    state.handle_input_bytes(b"y");
    assert!(state.transcript.copy.is_some());
    assert!(text(&mut state).contains("let value"));
}

#[test]
fn returning_to_a_pane_retains_folding_scroll_and_checks_conversation_identity() {
    let mut state = long_chat();
    let view = state.transcript.views.get_mut("pane_1").unwrap();
    view.toggle(1);
    view.offset = 12;
    state.compose(106, 24).unwrap();
    let top = state.transcript.views["pane_1"].top;
    // A quiet worker deliberately supplies no fresh document on returning.
    let (requests, _receive) = std::sync::mpsc::sync_channel(8);
    let (_send, results) = std::sync::mpsc::sync_channel(8);
    state.transcript.worker = Some(reader::Worker { requests, results });
    state.snapshot.as_mut().unwrap().focused_pane_id = None;
    let now = Instant::now();
    state.tick_transcript(now);
    assert!(state.transcript.target.is_none());
    state.snapshot.as_mut().unwrap().focused_pane_id = Some("pane_1".into());
    state.tick_transcript(now + Duration::from_secs(1));
    assert!(state.transcript_visible());
    state.compose(106, 24).unwrap();
    let view = &state.transcript.views["pane_1"];
    assert!(view.is_expanded(&view.document.blocks[1]));
    assert_eq!(view.top, top);
    state.snapshot.as_mut().unwrap().agents[0].terminal_title_stripped =
        Some("Different conversation | herrsch".into());
    state.tick_transcript(now + Duration::from_secs(2));
    assert!(
        !state.transcript_visible(),
        "old pane content must not leak into another conversation"
    );
}
