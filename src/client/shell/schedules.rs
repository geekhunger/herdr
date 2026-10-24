//! Local presentation only: the independent scheduler owns execution and persistence.
use super::*;
use serde::Deserialize;
use std::io::Read;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

#[derive(Clone, Debug, Default, Deserialize, PartialEq)]
pub(super) struct Job {
    name: String,
    enabled: bool,
    status: String,
    next: String,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq)]
pub(super) struct Snapshot {
    generated_at: f64,
    total: usize,
    active: usize,
    jobs: Vec<Job>,
}

pub(super) struct Schedules {
    path: Option<std::path::PathBuf>,
    checked_at: Option<Instant>,
    pub(super) snapshot: Option<Snapshot>,
    pub(super) healthy: bool,
}

impl Default for Schedules {
    fn default() -> Self {
        let path = std::env::var_os("HERDR_SCHEDULES_HOME")
            .map(std::path::PathBuf::from)
            .or_else(|| {
                std::env::var_os("HOME")
                    .or_else(|| std::env::var_os("USERPROFILE"))
                    .map(|home| std::path::PathBuf::from(home).join(".local/share/herdr-schedules"))
            })
            .map(|root| root.join("sidebar.json"));
        Self {
            path,
            checked_at: None,
            snapshot: None,
            healthy: false,
        }
    }
}

impl Schedules {
    pub(super) fn visible(&self) -> bool {
        self.checked_at.is_some()
    }

    // Once per second per client, outside all render/pane loops. Read at most 128 KiB.
    pub(super) fn refresh(&mut self, now: Instant) -> bool {
        if self
            .checked_at
            .is_some_and(|at| now.saturating_duration_since(at) < Duration::from_secs(1))
        {
            return false;
        }
        self.checked_at = Some(now);
        let snapshot = self.path.as_ref().and_then(|path| {
            let file = std::fs::File::open(path).ok()?;
            let mut bytes = Vec::new();
            file.take(131_073).read_to_end(&mut bytes).ok()?;
            if bytes.len() > 131_072 {
                return None;
            }
            serde_json::from_slice::<Snapshot>(&bytes).ok()
        });
        let unix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();
        let healthy = snapshot.as_ref().is_some_and(|data| {
            data.generated_at.is_finite() && (0.0..20.0).contains(&(unix - data.generated_at))
        });
        let changed = self.snapshot != snapshot || self.healthy != healthy;
        self.snapshot = snapshot;
        self.healthy = healthy;
        changed
    }
}

pub(super) fn sections(area: Rect, collapsed: bool) -> (Rect, Rect) {
    if collapsed || area.height < 15 || area.width < 18 {
        return (area, Rect::default());
    }
    let height = (area.height / 3).clamp(6, 15);
    (
        Rect::new(area.x, area.y, area.width, area.height - height),
        Rect::new(area.x, area.bottom() - height, area.width, height),
    )
}

pub(super) fn render(buffer: &mut Buffer, area: Rect, data: &Schedules, palette: &Palette) {
    if area.is_empty() {
        return;
    }
    super::render::render_sidebar_background(buffer, area, palette);
    let width = area.width.saturating_sub(1);
    let dim = Style::default().fg(palette.overlay0);
    super::render::put_text(
        buffer,
        area.x,
        area.y,
        width,
        &"─".repeat(usize::from(width)),
        Style::default().fg(palette.surface_dim),
    );
    let heading = dim.add_modifier(Modifier::BOLD);
    let count = data
        .snapshot
        .as_ref()
        .map(|snapshot| format!("{}/{}", snapshot.active, snapshot.total));
    let count_width = count
        .as_deref()
        .map_or(0, |text| text.len().min(usize::from(width)) as u16);
    super::render::put_text(
        buffer,
        area.x,
        area.y + 1,
        width.saturating_sub(count_width + u16::from(count_width > 0)),
        " schedules",
        heading,
    );
    if let Some(count) = count {
        super::render::put_text(
            buffer,
            area.x + width.saturating_sub(count_width),
            area.y + 1,
            count_width,
            &count,
            heading,
        );
    }
    let mut line = |y, text: &str, style| {
        super::render::put_text(buffer, area.x, y, width, text, style);
    };
    let footer = area.bottom() - 1;
    let mut y = area.y + 2;
    if !data.healthy {
        line(
            y,
            " ! Check service · C",
            Style::default().fg(palette.yellow),
        );
        y += 1;
    }
    let jobs = data
        .snapshot
        .as_ref()
        .map(|snapshot| snapshot.jobs.as_slice())
        .unwrap_or_default();
    let capacity = usize::from(footer.saturating_sub(y) / 2);
    let total = data.snapshot.as_ref().map_or(0, |snapshot| snapshot.total);
    for job in jobs.iter().take(capacity) {
        let (icon, color) = match job.status.as_str() {
            "running" => ("▶", palette.yellow),
            "queued" => ("…", palette.yellow),
            "failed" | "timed_out" | "interrupted" => ("!", palette.red),
            "succeeded" => ("✓", palette.green),
            "cancelled" => ("×", palette.overlay0),
            _ => (if job.enabled { "●" } else { "Ⅱ" }, palette.overlay0),
        };
        let name: String = job.name.chars().filter(|c| !c.is_control()).collect();
        line(y, &format!(" {icon} {name}"), Style::default().fg(color));
        let state = if job.enabled { "on" } else { "off" };
        let next: String = job.next.chars().filter(|c| !c.is_control()).collect();
        line(y + 1, &format!("   {state} · {next}"), dim);
        y += 2;
    }
    let more = total.saturating_sub(capacity);
    if more > 0 {
        line(footer, &format!(" +{more}"), dim);
    }
}

impl ClientShellState {
    pub(crate) fn tick_schedules(&mut self, now: Instant) -> bool {
        self.schedules.refresh(now)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sidebar_toggle_stays_at_window_bottom_with_schedules() {
        for rows in [14, 28, 44] {
            for unavailable in [false, true] {
                for remote in [false, true] {
                    let mut state =
                        ClientShellState::new(ClientShellConfig::from_config(&Config::default()));
                    if remote {
                        state.set_endpoint_catalog(&[crate::client::endpoint::SavedSshEndpoint {
                            id: crate::client::endpoint::ProfileId::parse(
                                "0123456789abcdef0123456789abcdef",
                            )
                            .unwrap(),
                            label: "Build".into(),
                            target: "build.example".into(),
                            session: "qa".into(),
                            enabled: true,
                        }]);
                    }
                    state.set_snapshot(Box::new(super::super::tests::snapshot()));
                    if !unavailable {
                        state.set_pane_surface(super::super::tests::surface());
                    }
                    state.schedules = Schedules {
                        path: None,
                        checked_at: Some(Instant::now()),
                        snapshot: None,
                        healthy: true,
                    };
                    let frame = state.compose(106, rows).expect("sidebar frame");
                    let area = state.layout(106, rows).sidebar;
                    let toggle = state.hits.sidebar_toggle;
                    assert_eq!(toggle, Rect::new(area.right() - 2, rows - 1, 1, 1));
                    let buffer = frame.to_ratatui_buffer().unwrap();
                    assert_eq!(buffer[(toggle.x, toggle.y)].symbol(), "«");
                    assert_eq!(
                        buffer
                            .content()
                            .iter()
                            .filter(|cell| cell.symbol() == "«")
                            .count(),
                        1
                    );
                    let outcome =
                        state.handle_raw_events(vec![crate::raw_input::RawInputEvent::Mouse(
                            crossterm::event::MouseEvent {
                                kind: crossterm::event::MouseEventKind::Down(
                                    crossterm::event::MouseButton::Left,
                                ),
                                column: toggle.x,
                                row: toggle.y,
                                modifiers: crossterm::event::KeyModifiers::empty(),
                            },
                        )]);
                    assert!(state.sidebar_collapsed);
                    assert!(outcome.resize && outcome.repaint);
                }
            }
        }
    }

    #[test]
    fn panel_geometry_preserves_agent_and_workspace_space() {
        for height in 0..80 {
            let area = Rect::new(0, 1, 30, height);
            let (main, schedules) = sections(area, false);
            assert_eq!(main.height + schedules.height, area.height);
            assert!(main.height >= height.min(9));
            if schedules.height > 0 {
                assert_eq!(main.bottom(), schedules.y);
                assert_eq!(schedules.bottom(), area.bottom());
            }
            assert_eq!(sections(area, true), (area, Rect::default()));
        }
    }

    #[test]
    fn refresh_detects_service_loss_and_throttles_io() {
        let path =
            std::env::temp_dir().join(format!("herdr-schedules-{}.json", std::process::id()));
        let mut data = Schedules {
            path: Some(path.clone()),
            checked_at: None,
            snapshot: None,
            healthy: false,
        };
        let unix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs_f64();
        std::fs::write(
            &path,
            format!(r#"{{"generated_at":{unix},"total":1,"active":1,"jobs":[]}}"#),
        )
        .unwrap();
        let now = Instant::now();
        assert!(data.refresh(now));
        assert!(data.healthy);
        std::fs::remove_file(&path).unwrap();
        assert!(!data.refresh(now + Duration::from_millis(50)));
        assert!(data.refresh(now + Duration::from_secs(2)));
        assert!(!data.healthy);
    }

    #[test]
    fn overview_renders_status_and_overflow_at_narrow_widths() {
        let data = Schedules {
            healthy: true,
            snapshot: Some(Snapshot {
                generated_at: 0.0,
                total: 15,
                active: 1,
                jobs: (0..15)
                    .map(|n| Job {
                        name: format!("Job {n}"),
                        enabled: n == 0,
                        status: "failed".into(),
                        next: "26.10. 09:00".into(),
                    })
                    .collect(),
            }),
            ..Schedules::default()
        };
        for width in [18, 24, 36] {
            let area = Rect::new(0, 0, width, 10);
            let mut buffer = Buffer::empty(area);
            let palette = Palette::catppuccin();
            render(&mut buffer, area, &data, &palette);
            let text = buffer
                .content()
                .iter()
                .map(|c| c.symbol())
                .collect::<String>();
            assert!(text.contains("schedules"));
            let header = (0..width - 1)
                .map(|x| buffer[(x, 1)].symbol())
                .collect::<String>();
            assert!(header.ends_with("1/15"));
            assert_eq!(buffer[(1, 1)].fg, palette.overlay0);
            assert!(text.contains("! Job 0"));
            assert!(text.contains("+12"));
        }
    }
}
