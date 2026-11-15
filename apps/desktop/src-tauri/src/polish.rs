//! "Polish with Opus": turns the person's rough standing instructions into clear rules.
//! One headless Claude Code call with no tools and no MCP servers; it only returns text,
//! which the page shows as a suggestion the person accepts or ignores.

use serde_json::Value;
use std::io::{Read, Write};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};
use tauri::Manager;

use crate::MAX_PROMPT_CHARS;

pub const POLISH_MODEL: &str = "claude-opus-5-5";
const POLISH_TIMEOUT: Duration = Duration::from_secs(180);

const SYSTEM_PROMPT: &str = r#"You rewrite a person's rough notes into clear standing instructions for an AI video editor.
The notes may be messy, misspelled, repetitive or in any order.

Rules:
- Keep every requirement and preference the person stated. Do not add new requirements,
  numbers, styles or assumptions of your own.
- Fix spelling and grammar, merge duplicates, and make each rule specific and actionable.
- If two notes conflict, keep the later one. If a note is too vague to act on, keep it and
  mark it "(unclear: ...)" with a short question.
- Order: format and length; story and content; pacing and style; transitions and effects;
  audio and music; captions and on-screen text; branding; anything else.
- Write in the same language as the notes.

Output only the rules, one per line, each starting with "- ". No title, preamble, headings,
bold text or closing remarks."#;

/// Parses Claude Code's `--output-format json` reply into the polished text.
pub fn polished_text(reply: &Value) -> Result<String, String> {
    if reply
        .get("is_error")
        .and_then(Value::as_bool)
        .unwrap_or(true)
    {
        let detail = reply.get("result").and_then(Value::as_str).unwrap_or("");
        return Err(if detail.is_empty() {
            "Opus could not polish the instructions".into()
        } else {
            format!("Opus could not polish the instructions: {detail}")
        });
    }
    let text = reply
        .get("result")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    if text.is_empty() {
        return Err("Opus returned no instructions".into());
    }
    Ok(text.chars().take(MAX_PROMPT_CHARS).collect())
}

pub fn polish_instructions_blocking(app: tauri::AppHandle, text: String) -> Result<String, String> {
    let text = text.trim().to_string();
    if text.is_empty() {
        return Err("Write some instructions first".into());
    }
    if text.chars().count() > MAX_PROMPT_CHARS {
        return Err(format!(
            "Keep the instructions under {MAX_PROMPT_CHARS} characters"
        ));
    }
    let claude = crate::setup::trusted_claude()?;
    // Run outside any repository so no project CLAUDE.md or settings shape the rewrite.
    let dir = app
        .path()
        .app_local_data_dir()
        .map_err(|e| e.to_string())?
        .join("polish");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;

    let mut command = Command::new(claude);
    command
        .current_dir(&dir)
        .args(["-p", "--output-format", "json"])
        .args(["--model", POLISH_MODEL, "--effort", "high"])
        .args(["--tools", "", "--strict-mcp-config"])
        .args(["--no-session-persistence", "--disable-slash-commands"])
        .args(["--system-prompt", SYSTEM_PROMPT])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    let mut child = command
        .spawn()
        .map_err(|e| format!("Could not start Claude Code: {e}"))?;
    // The notes go over stdin, so no shell or argument quoting ever touches them.
    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(text.as_bytes());
    }
    let read_all = |pipe: Option<Box<dyn Read + Send>>| {
        thread::spawn(move || {
            let mut out = String::new();
            if let Some(mut pipe) = pipe {
                let _ = pipe.read_to_string(&mut out);
            }
            out
        })
    };
    let stdout = read_all(
        child
            .stdout
            .take()
            .map(|p| Box::new(p) as Box<dyn Read + Send>),
    );
    let stderr = read_all(
        child
            .stderr
            .take()
            .map(|p| Box::new(p) as Box<dyn Read + Send>),
    );

    let deadline = Instant::now() + POLISH_TIMEOUT;
    loop {
        if child.try_wait().map_err(|e| e.to_string())?.is_some() {
            break;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err("Polishing timed out after 3 minutes".into());
        }
        thread::sleep(Duration::from_millis(200));
    }
    let out = stdout.join().unwrap_or_default();
    let err = stderr.join().unwrap_or_default();
    let reply: Value = serde_json::from_str(out.trim()).map_err(|_| {
        let tail: String = err
            .trim()
            .chars()
            .rev()
            .take(400)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect();
        if tail.is_empty() {
            "Claude Code stopped without a reply".to_string()
        } else {
            format!("Claude Code stopped without a reply: {tail}")
        }
    })?;
    polished_text(&reply)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn returns_only_a_successful_non_empty_rewrite() {
        assert_eq!(
            polished_text(&json!({"is_error": false, "result": "  - Keep it under 30 seconds.\n"})),
            Ok("- Keep it under 30 seconds.".to_string())
        );
        assert!(polished_text(&json!({"is_error": false, "result": "   "})).is_err());
        assert!(
            polished_text(&json!({"is_error": true, "result": "overloaded"}))
                .unwrap_err()
                .contains("overloaded")
        );
        assert!(polished_text(&json!({"result": "no status"})).is_err());
    }
}
