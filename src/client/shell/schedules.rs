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
    let mut line = |y, text: &str, style| {
        super::render::put_text(buffer, area.x, y, width, text, style);
    };
    let dim = Style::default().fg(palette.overlay0);
    line(
        area.y,
        &"─".repeat(usize::from(width)),
        Style::default().fg(palette.surface_dim),
    );
    let title = if let Some(snapshot) = &data.snapshot {
        format!(" schedules {}/{}", snapshot.active, snapshot.total)
    } else {
        " schedules".to_owned()
    };
    line(
        area.y + 1,
        &title,
        Style::default()
            .fg(palette.text)
            .add_modifier(Modifier::BOLD),
    );
    let footer = area.bottom() - 1;
    let mut y = area.y + 2;
    if !data.healthy {
        line(
            y,
            " ! Dienst prüfen · C",
            Style::default().fg(palette.yellow),
        );
        y += 1;
    }
    let jobs = data
        .snapshot
        .as_ref()
        .map(|snapshot| snapshot.jobs.as_slice())
        .unwrap_or_default();
    if jobs.is_empty() && y < footer {
        line(y, " Keine Schedules", dim);
    }
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
        let state = if job.enabled { "an" } else { "aus" };
        let next: String = job.next.chars().filter(|c| !c.is_control()).collect();
        line(y + 1, &format!("   {state} · {next}"), dim);
        y += 2;
    }
    let more = total.saturating_sub(capacity);
    let footer_text = if more > 0 {
        format!(" +{more} · Prefix+C öffnen")
    } else {
        " Prefix+C verwalten".to_owned()
    };
    line(footer, &footer_text, dim);
}

#[cfg(test)]
mod tests {
    use super::*;

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
        let mut data = Schedules::default();
        data.healthy = true;
        data.snapshot = Some(Snapshot {
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
        });
        for width in [18, 24, 36] {
            let area = Rect::new(0, 0, width, 10);
            let mut buffer = Buffer::empty(area);
            render(&mut buffer, area, &data, &Palette::catppuccin());
            let text = buffer
                .content()
                .iter()
                .map(|c| c.symbol())
                .collect::<String>();
            assert!(text.contains("schedules 1/15"));
            assert!(text.contains("! Job 0"));
            assert!(text.contains("+12"));
        }
    }
}

impl ClientShellState {
    pub(crate) fn tick_schedules(&mut self, now: Instant) -> bool {
        self.schedules.refresh(now)
    }
}
