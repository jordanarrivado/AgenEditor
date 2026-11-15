//! Claude plan usage for the header badge. Claude Code reports the account's rate-limit
//! windows (5-hour session and weekly) as a `rate_limit_event` in its stream output; every
//! editing run passes through here, and the latest snapshot is kept on disk so it can be
//! shown at startup with its age. Nothing is estimated: numbers come only from those events.

use serde_json::{json, Value};
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::{Emitter, Manager};

pub type UsageSlot = Arc<Mutex<Option<Value>>>;

const REFRESH_TIMEOUT: Duration = Duration::from_secs(60);
const REFRESH_MODEL: &str = "claude-haiku-5-5";

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn window(info: &Value, name: &str) -> Value {
    let Some(w) = info.pointer(&format!("/unifiedWindows/{name}")) else {
        return Value::Null;
    };
    let utilization = w
        .get("utilization")
        .and_then(Value::as_f64)
        .filter(|u| u.is_finite() && (0.0..=10.0).contains(u));
    match utilization {
        None => Value::Null,
        Some(u) => json!({
            "utilization": u,
            "resets_at": w.get("resetsAt").and_then(Value::as_u64),
            "status": w.get("status").and_then(Value::as_str).unwrap_or("allowed"),
        }),
    }
}

/// Turns one `rate_limit_event` line into the snapshot the page shows, or None if it isn't one.
pub fn snapshot_from_event(line: &Value, captured_at: u64) -> Option<Value> {
    if line.get("type").and_then(Value::as_str) != Some("rate_limit_event") {
        return None;
    }
    let info = line.get("rate_limit_info")?;
    let five_hour = window(info, "five_hour");
    let seven_day = window(info, "seven_day");
    if five_hour.is_null() && seven_day.is_null() {
        return None;
    }
    Some(json!({
        "status": info.get("status").and_then(Value::as_str).unwrap_or("allowed"),
        "five_hour": five_hour,
        "seven_day": seven_day,
        "using_overage": info.get("isUsingOverage").and_then(Value::as_bool).unwrap_or(false),
        "captured_at": captured_at,
    }))
}

fn store_path(app: &tauri::AppHandle) -> Option<PathBuf> {
    app.path()
        .app_local_data_dir()
        .ok()
        .map(|dir| dir.join("claude_usage.json"))
}

/// The latest snapshot: from memory, else from disk (so it survives restarts).
pub fn current(app: &tauri::AppHandle, slot: &UsageSlot) -> Value {
    if let Some(value) = slot.lock().ok().and_then(|guard| guard.clone()) {
        return value;
    }
    let loaded = store_path(app)
        .and_then(|path| fs::read_to_string(path).ok())
        .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
        .filter(Value::is_object);
    if let (Some(value), Ok(mut guard)) = (&loaded, slot.lock()) {
        *guard = Some(value.clone());
    }
    loaded.unwrap_or(Value::Null)
}

/// Serialises writers so a streaming run and a manual refresh never interleave.
static WRITE_LOCK: Mutex<()> = Mutex::new(());

/// Writes the file whole or not at all: a reader sees the old snapshot or the new one, never a
/// truncated file. Creates the folder first.
pub fn write_atomic(path: &Path, text: &str) -> std::io::Result<()> {
    let _guard = WRITE_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir)?;
    }
    let temporary = path.with_extension("json.tmp");
    fs::write(&temporary, text)?;
    fs::rename(&temporary, path)
}

fn save(app: &tauri::AppHandle, slot: &UsageSlot, value: Value) {
    if let Some(path) = store_path(app) {
        let _ = write_atomic(&path, &value.to_string());
    }
    if let Ok(mut guard) = slot.lock() {
        *guard = Some(value.clone());
    }
    let _ = app.emit("claude-usage", value);
}

/// Records a rate-limit snapshot seen during a run, keeping the last run's details.
pub fn record_event(app: &tauri::AppHandle, slot: &UsageSlot, line: &Value) {
    if let Some(mut snapshot) = snapshot_from_event(line, now_secs()) {
        if let Some(last) = current(app, slot).get("last_run").cloned() {
            snapshot["last_run"] = last;
        }
        save(app, slot, snapshot);
    }
}

/// Records what a finished run reported (model, stage, list-price cost, duration).
pub fn record_run(
    app: &tauri::AppHandle,
    slot: &UsageSlot,
    stage: &str,
    model: &str,
    result: &Value,
) {
    let mut snapshot = current(app, slot);
    if !snapshot.is_object() {
        return; // no plan numbers yet; keep showing "no data" rather than a half snapshot
    }
    snapshot["last_run"] = json!({
        "stage": stage,
        "model": model,
        "cost_usd": result.get("total_cost_usd").and_then(Value::as_f64),
        "duration_ms": result.get("duration_ms").and_then(Value::as_u64),
        "finished_at": now_secs(),
    });
    save(app, slot, snapshot);
}

/// Sends one tiny Haiku request only to read the current limits, then stops it.
pub fn refresh_blocking(app: tauri::AppHandle, slot: UsageSlot) -> Result<Value, String> {
    let claude = crate::setup::trusted_claude()?;
    let dir = app
        .path()
        .app_local_data_dir()
        .map_err(|e| e.to_string())?
        .join("polish");
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let mut command = Command::new(claude);
    command
        .current_dir(&dir)
        .args(["-p", "--output-format", "stream-json", "--verbose"])
        .args([
            "--model",
            REFRESH_MODEL,
            "--tools",
            "",
            "--strict-mcp-config",
        ])
        .args(["--no-session-persistence", "--disable-slash-commands"])
        .arg("Reply with OK.")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let mut child = command
        .spawn()
        .map_err(|e| format!("Could not start Claude Code: {e}"))?;
    let stdout = child.stdout.take().ok_or("Claude Code gave no output")?;
    let (tx, rx) = std::sync::mpsc::channel::<Value>();
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Ok(value) = serde_json::from_str::<Value>(&line) {
                if snapshot_from_event(&value, 0).is_some() {
                    let _ = tx.send(value);
                    return;
                }
            }
        }
    });
    let deadline = Instant::now() + REFRESH_TIMEOUT;
    let event = rx.recv_timeout(deadline.saturating_duration_since(Instant::now()));
    let _ = child.kill();
    let _ = child.wait();
    match event {
        Ok(line) => {
            record_event(&app, &slot, &line);
            Ok(current(&app, &slot))
        }
        Err(_) => Err(
            "Claude Code did not report usage. Check that you are signed in (Home → Set up this PC)."
                .into(),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn atomic_writes_replace_the_file_whole_and_leave_no_temp_file() {
        let dir = std::env::temp_dir().join(format!("ageneditor-usage-test-{}", now_secs()));
        let path = dir.join("nested").join("claude_usage.json"); // folder does not exist yet
        write_atomic(&path, r#"{"a":1}"#).unwrap();
        write_atomic(&path, r#"{"a":2}"#).unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), r#"{"a":2}"#);
        assert!(!path.with_extension("json.tmp").exists());
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn reads_both_windows_from_a_rate_limit_event() {
        let line = json!({"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "isUsingOverage": false,
            "unifiedWindows": {
                "five_hour": {"utilization": 0.26, "resetsAt": 1794766800u64, "status": "allowed"},
                "seven_day": {"utilization": 0.16, "resetsAt": 1795082400u64, "status": "allowed"}
            }
        }});
        let snapshot = snapshot_from_event(&line, 42).unwrap();
        assert_eq!(snapshot["five_hour"]["utilization"], json!(0.26));
        assert_eq!(snapshot["seven_day"]["resets_at"], json!(1795082400u64));
        assert_eq!(snapshot["captured_at"], json!(42));
    }

    #[test]
    fn ignores_other_lines_and_nonsense_numbers() {
        assert!(snapshot_from_event(&json!({"type": "assistant"}), 0).is_none());
        let bad = json!({"type": "rate_limit_event", "rate_limit_info": {
            "unifiedWindows": {"five_hour": {"utilization": "lots"}}
        }});
        assert!(snapshot_from_event(&bad, 0).is_none());
    }
}
