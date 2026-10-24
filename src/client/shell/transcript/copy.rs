use super::*;

pub(in crate::client::shell) struct CopyCursor {
    pub pane_id: String,
    pub row: usize,
    pub col: u16,
    pub reveal: bool,
    anchor: Option<(usize, u16)>,
    linewise: bool,
    matches: Vec<(usize, u16, u16)>,
}

impl ClientShellState {
    pub(in crate::client::shell) fn start_transcript_copy(&mut self) {
        let Some(pane_id) = self.transcript.active_pane.clone() else {
            return;
        };
        let Some(view) = self.transcript.views.get_mut(&pane_id) else {
            return;
        };
        let row = (view.top + usize::from(self.transcript.body.height.saturating_sub(1)))
            .min(view.rows.len().saturating_sub(1));
        view.selection = None;
        self.transcript.copy = Some(CopyCursor {
            pane_id,
            row,
            col: 0,
            reveal: true,
            anchor: None,
            linewise: false,
            matches: Vec::new(),
        });
    }

    pub(in crate::client::shell) fn exit_transcript_copy(
        &mut self,
        copy: bool,
        outcome: &mut ClientShellInput,
    ) -> bool {
        let Some(cursor) = self.transcript.copy.take() else {
            return false;
        };
        if let Some(view) = self.transcript.views.get_mut(&cursor.pane_id) {
            if copy {
                if let Some(text) = view.selected_text() {
                    outcome
                        .actions
                        .push(ClientShellAction::ClipboardWrite(text.into_bytes()));
                }
            }
            view.selection = None;
            if let Some(document) = view.pending_document.take() {
                view.update(document);
            }
        }
        self.copy_mode = None;
        self.selection = None;
        self.reset_copy_pipeline();
        self.mode = ClientShellMode::Terminal;
        outcome.repaint = true;
        true
    }

    pub(in crate::client::shell) fn route_transcript_copy_key(
        &mut self,
        key: &crate::input::TerminalKey,
        outcome: &mut ClientShellInput,
    ) -> bool {
        if self.transcript.copy.is_none() {
            return false;
        }
        outcome.repaint = true;
        if self
            .copy_mode
            .as_ref()
            .is_some_and(|copy| copy.search_prompt.is_some())
        {
            match key.code {
                KeyCode::Esc => {
                    self.copy_mode.as_mut().unwrap().search_prompt = None;
                }
                KeyCode::Enter => {
                    let prompt = self
                        .copy_mode
                        .as_mut()
                        .unwrap()
                        .search_prompt
                        .take()
                        .unwrap();
                    self.search_transcript(prompt.query.to_string(), prompt.direction, false);
                }
                _ => {
                    self.copy_mode
                        .as_mut()
                        .unwrap()
                        .search_prompt
                        .as_mut()
                        .unwrap()
                        .query
                        .handle_key(key);
                }
            }
            return true;
        }
        let command = crate::copy_mode::copy_mode_command_char(key.clone());
        if key.code == KeyCode::Enter || command == Some('y') {
            self.exit_transcript_copy(true, outcome);
            return true;
        }
        if command == Some('q') {
            self.exit_transcript_copy(false, outcome);
            return true;
        }
        if key.code == KeyCode::Esc {
            let cursor = self.transcript.copy.as_mut().unwrap();
            let view = self.transcript.views.get_mut(&cursor.pane_id).unwrap();
            let copy = self.copy_mode.as_mut().unwrap();
            if view.selection.is_some() || !copy.search_query.is_empty() {
                view.selection = None;
                cursor.anchor = None;
                cursor.matches.clear();
                copy.search_query.clear();
                copy.search_total = 0;
                copy.search_current_global = None;
            } else {
                view.offset = 0;
                view.anchor = None;
                view.reading_anchor = None;
                self.exit_transcript_copy(false, outcome);
            }
            return true;
        }
        if matches!(command, Some('/' | '?')) {
            self.open_copy_search(if command == Some('/') {
                crate::api::schema::PaneCopySearchDirection::Forward
            } else {
                crate::api::schema::PaneCopySearchDirection::Backward
            });
            return true;
        }
        let query = self.copy_mode.as_ref().unwrap().search_query.clone();
        if !query.is_empty() {
            let direction = match command {
                Some('k' | 'L' | 'n') => Some(crate::api::schema::PaneCopySearchDirection::Forward),
                Some('l' | 'K' | 'N') => {
                    Some(crate::api::schema::PaneCopySearchDirection::Backward)
                }
                _ => None,
            };
            if let Some(direction) = direction {
                self.search_transcript(query, direction, true);
                return true;
            }
        }
        let cursor = self.transcript.copy.as_mut().unwrap();
        let view = self.transcript.views.get_mut(&cursor.pane_id).unwrap();
        if matches!(key.code, KeyCode::Char('c' | 'C'))
            && matches!(key.modifiers, KeyModifiers::CONTROL | KeyModifiers::SUPER)
        {
            if let Some(text) = view.selected_text() {
                outcome
                    .actions
                    .push(ClientShellAction::ClipboardWrite(text.into_bytes()));
            }
            return true;
        }
        if key.code == KeyCode::Char('t') && key.modifiers == KeyModifiers::CONTROL {
            view.toggle_all();
            cursor.anchor = None;
            cursor.reveal = true;
            return true;
        }
        if matches!(command, Some('v' | ' ' | 'V')) {
            cursor.anchor = Some((cursor.row, cursor.col));
            cursor.linewise = command == Some('V');
        } else {
            let page = usize::from(self.transcript.body.height.saturating_sub(2).max(1));
            let control = key.modifiers.contains(KeyModifiers::CONTROL);
            let step = if matches!(key.code, KeyCode::PageUp | KeyCode::PageDown)
                || key.modifiers.contains(KeyModifiers::ALT)
            {
                page
            } else if control {
                page.div_ceil(2)
            } else {
                1
            };
            if key.modifiers == KeyModifiers::SHIFT
                && matches!(key.code, KeyCode::Up | KeyCode::Down)
            {
                let down = key.code == KeyCode::Down;
                cursor.row = if down {
                    (cursor.row + 1).min(view.rows.len().saturating_sub(1))
                } else {
                    cursor.row.saturating_sub(1)
                };
                while cursor.row > 0
                    && cursor.row + 1 < view.rows.len()
                    && !view.rows[cursor.row].text.trim().is_empty()
                {
                    cursor.row = if down { cursor.row + 1 } else { cursor.row - 1 };
                }
            } else {
                match (key.code, command) {
                    (KeyCode::Up | KeyCode::PageUp, _) | (_, Some('k')) => {
                        cursor.row = cursor.row.saturating_sub(step)
                    }
                    (KeyCode::Down | KeyCode::PageDown, _) | (_, Some('j')) => {
                        cursor.row = cursor.row.saturating_add(step)
                    }
                    (KeyCode::Char('u' | 'b'), _) if control => {
                        cursor.row = cursor
                            .row
                            .saturating_sub(if key.code == KeyCode::Char('b') {
                                page
                            } else {
                                step
                            })
                    }
                    (KeyCode::Char('d' | 'f'), _) if control => {
                        cursor.row = cursor
                            .row
                            .saturating_add(if key.code == KeyCode::Char('f') {
                                page
                            } else {
                                step
                            })
                    }
                    (KeyCode::Left, _) | (_, Some('h')) => {
                        cursor.col = cursor.col.saturating_sub(1)
                    }
                    (KeyCode::Right, _) | (_, Some('l')) => {
                        cursor.col = cursor.col.saturating_add(1)
                    }
                    (_, Some('g')) => cursor.row = 0,
                    (_, Some('G')) => cursor.row = view.rows.len().saturating_sub(1),
                    (KeyCode::Home, _) | (_, Some('0')) => cursor.col = 0,
                    (KeyCode::End, _) | (_, Some('$')) => cursor.col = u16::MAX,
                    (_, Some('w' | 'W' | 'b' | 'B' | 'e' | 'E')) => {
                        if let Some(row) = view.rows.get(cursor.row) {
                            let regex = regex::Regex::new(r"\S+").expect("static word regex");
                            let positions: Vec<_> = regex
                                .find_iter(&row.text)
                                .map(|word| {
                                    let start =
                                        UnicodeWidthStr::width(&row.text[..word.start()]) as u16;
                                    let end = UnicodeWidthStr::width(&row.text[..word.end()])
                                        .saturating_sub(1)
                                        as u16;
                                    (start, end)
                                })
                                .collect();
                            cursor.col = match command {
                                Some('b' | 'B') => positions
                                    .iter()
                                    .rev()
                                    .find(|(start, _)| *start < cursor.col)
                                    .map_or(0, |(start, _)| *start),
                                Some('e' | 'E') => positions
                                    .iter()
                                    .find(|(_, end)| *end > cursor.col)
                                    .map_or(cursor.col, |(_, end)| *end),
                                _ => positions
                                    .iter()
                                    .find(|(start, _)| *start > cursor.col)
                                    .map_or(cursor.col, |(start, _)| *start),
                            };
                        }
                    }
                    _ => {}
                }
            }
        }
        cursor.row = cursor.row.min(view.rows.len().saturating_sub(1));
        let width =
            view.rows
                .get(cursor.row)
                .map_or(0, |row| {
                    UnicodeWidthStr::width(row.text.as_str()).saturating_sub(1)
                })
                .min(usize::from(self.transcript.body.width.saturating_sub(2))) as u16;
        cursor.col = cursor.col.min(width);
        cursor.reveal = true;
        view.selection = cursor.anchor.map(|anchor| {
            if cursor.linewise {
                (
                    (anchor.0.min(cursor.row), 0),
                    (
                        anchor.0.max(cursor.row),
                        self.transcript.body.width.saturating_sub(2),
                    ),
                )
            } else {
                (anchor, (cursor.row, cursor.col))
            }
        });
        true
    }

    fn search_transcript(
        &mut self,
        query: String,
        direction: crate::api::schema::PaneCopySearchDirection,
        repeat: bool,
    ) {
        let Some(cursor) = self.transcript.copy.as_mut() else {
            return;
        };
        let Some(view) = self.transcript.views.get_mut(&cursor.pane_id) else {
            return;
        };
        if !repeat {
            cursor.matches.clear();
            if !query.is_empty() {
                if let Ok(regex) = regex::RegexBuilder::new(&regex::escape(&query))
                    .case_insensitive(!query.chars().any(char::is_uppercase))
                    .build()
                {
                    for (index, row) in view.rows.iter().enumerate() {
                        for found in regex.find_iter(&row.text) {
                            cursor.matches.push((
                                index,
                                UnicodeWidthStr::width(&row.text[..found.start()]) as u16,
                                UnicodeWidthStr::width(&row.text[..found.end()]).saturating_sub(1)
                                    as u16,
                            ));
                        }
                    }
                }
            }
        }
        let point = (cursor.row, cursor.col);
        let index = match direction {
            crate::api::schema::PaneCopySearchDirection::Forward => cursor
                .matches
                .iter()
                .position(|(row, col, _)| {
                    if repeat {
                        (*row, *col) > point
                    } else {
                        (*row, *col) >= point
                    }
                })
                .or((!cursor.matches.is_empty()).then_some(0)),
            crate::api::schema::PaneCopySearchDirection::Backward => cursor
                .matches
                .iter()
                .rposition(|(row, col, _)| {
                    if repeat {
                        (*row, *col) < point
                    } else {
                        (*row, *col) <= point
                    }
                })
                .or_else(|| cursor.matches.len().checked_sub(1)),
        };
        let copy = self.copy_mode.as_mut().unwrap();
        copy.search_query = query;
        copy.search_direction = Some(direction);
        copy.search_total = cursor.matches.len() as u64;
        copy.search_current_global = index.map(|n| n as u64);
        if let Some((row, start, end)) = index.and_then(|index| cursor.matches.get(index)).copied()
        {
            cursor.row = row;
            cursor.col = start;
            cursor.reveal = true;
            cursor.anchor = None;
            view.selection = Some(((row, start), (row, end)));
        }
    }
}
