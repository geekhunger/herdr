//! One bounded worker for the focused local conversation. Never runs in render.
use super::{Document, Target};
use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};
use std::sync::mpsc::{self, Receiver, SyncSender};

pub(super) struct Worker {
    pub requests: SyncSender<Target>,
    pub results: Receiver<(Target, Option<Document>)>,
}

impl Worker {
    pub fn start() -> Option<Self> {
        let helper = std::env::var_os("HERDR_TRANSCRIPT_READER")
            .map(std::path::PathBuf::from)
            .or_else(|| {
                std::env::var_os("HOME").map(|home| {
                    std::path::PathBuf::from(home)
                        .join(".config/herdr/scripts/transcript/reader.py")
                })
            })?;
        if !helper.is_file() {
            return None;
        }
        let (requests, receive_requests) = mpsc::sync_channel::<Target>(1);
        let (send_results, results) = mpsc::sync_channel(1);
        std::thread::Builder::new()
            .name("herdr-transcript".into())
            .spawn(move || {
                let Ok(mut child) = Command::new("python3")
                    .arg("-u")
                    .arg(helper)
                    .stdin(Stdio::piped())
                    .stdout(Stdio::piped())
                    .stderr(Stdio::null())
                    .spawn()
                else {
                    return;
                };
                let mut input = child.stdin.take().expect("piped stdin");
                let mut output = BufReader::new(child.stdout.take().expect("piped stdout"));
                for target in receive_requests {
                    let Ok(mut request) = serde_json::to_vec(&target) else {
                        continue;
                    };
                    request.push(b'\n');
                    if input
                        .write_all(&request)
                        .and_then(|_| input.flush())
                        .is_err()
                    {
                        break;
                    }
                    let mut line = Vec::new();
                    let mut valid = false;
                    // Do not trust an accidental or broken helper to allocate without bound.
                    while line.len() <= 16 * 1024 * 1024 {
                        let Ok(available) = output.fill_buf() else {
                            break;
                        };
                        if available.is_empty() {
                            break;
                        }
                        let count = available
                            .iter()
                            .position(|byte| *byte == b'\n')
                            .map_or(available.len(), |n| n + 1);
                        line.extend_from_slice(&available[..count]);
                        output.consume(count);
                        if line.last() == Some(&b'\n') {
                            valid = true;
                            break;
                        }
                    }
                    if !valid {
                        break;
                    }
                    let value = serde_json::from_slice::<serde_json::Value>(&line).ok();
                    if value
                        .as_ref()
                        .is_some_and(|value| value["unchanged"] == true)
                    {
                        continue;
                    }
                    let document = value.and_then(|value| {
                        (value["ready"] == true)
                            .then(|| serde_json::from_value(value).ok())
                            .flatten()
                    });
                    if send_results.send((target, document)).is_err() {
                        break;
                    }
                }
                let _ = child.kill();
                let _ = child.wait();
            })
            .ok()?;
        Some(Self { requests, results })
    }
}
