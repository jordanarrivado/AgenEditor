//! Runs Claude Code headless as the editor. Claude gets no built-in tools, only the
//! AgenEditor MCP server, so every change it makes is a validated engine revision.

use serde_json::{json, Value};
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Arc;
use std::thread;
use std::time::Instant;
use tauri::{Emitter, Manager};

use crate::{engine_cli, AgentSlot, RunningAgent, Sessions, AGENT_TIMEOUT, MAX_PROMPT_CHARS};

/// Finds the native Claude Code executable (standalone install or the npm package's binary).
pub fn find_claude() -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(path) = std::env::var_os("PATH") {
        for dir in std::env::split_paths(&path) {
            candidates.push(dir.join("claude.exe"));
            candidates.push(dir.join("node_modules/@anthropic-ai/claude-code/bin/claude.exe"));
        }
    }
    if let Some(profile) = std::env::var_os("USERPROFILE") {
        candidates.push(PathBuf::from(profile).join(".local/bin/claude.exe"));
    }
    candidates.into_iter().find(|candidate| candidate.is_file())
}

pub fn valid_project_id(project_id: &str) -> bool {
    !project_id.is_empty()
        && project_id.len() <= 100
        && project_id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

/// The step of the Plan -> Execute -> Review workflow a run belongs to (spec §3.1). The stage
/// picks the model, the effort and which engine tools Claude may call.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Stage {
    Plan,
    Execute,
    Review,
}

/// Tools that change the project. Plan and Review may read, transcribe and validate (Review may
/// also render previews and run QA), but never change the edit.
const EDIT_TOOLS: &[&str] = &[
    "create_project",
    "register_asset",
    "create_timeline",
    "commit_timeline",
    "apply_revision",
    "revert_revision",
    "set_asset_labels",
    "add_sound_effect",
    "cancel_job",
];

impl Stage {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "plan" => Some(Self::Plan),
            "execute" => Some(Self::Execute),
            "review" => Some(Self::Review),
            _ => None,
        }
    }

    pub fn id(self) -> &'static str {
        match self {
            Self::Plan => "plan",
            Self::Execute => "execute",
            Self::Review => "review",
        }
    }

    /// Opus plans and reviews; Sonnet carries out the plan. All three use high effort.
    pub fn model(self) -> &'static str {
        match self {
            Self::Plan | Self::Review => "claude-opus-5-5",
            Self::Execute => "claude-sonnet-5-5",
        }
    }

    pub fn effort(self) -> &'static str {
        "high"
    }

    /// Engine tools this stage may not call, as Claude Code tool names.
    pub fn blocked_tools(self) -> Vec<String> {
        let mut blocked: Vec<&str> = match self {
            Self::Execute => Vec::new(),
            Self::Plan => [EDIT_TOOLS, &["start_render"]].concat(),
            Self::Review => EDIT_TOOLS.to_vec(),
        };
        blocked.sort_unstable();
        blocked
            .into_iter()
            .map(|tool| format!("mcp__ageneditor__{tool}"))
            .collect()
    }

    fn instructions(self) -> &'static str {
        match self {
            Self::Plan => {
                r#"Current stage: PLAN (Claude Opus 5.5). You plan; you do not edit. Tools that commit,
render or add sounds are switched off in this stage.
- Do steps 1-3, then read get_timeline, and list_transitions / list_sound_effects if the edit
  calls for them.
- Write the edit plan as a numbered list in playback order: which file, the exact quoted words
  (or time range), and a few words on why. Then any overlays, music, transitions and sound
  effects, and the expected length. Check a speech-based cut with submit_edit_plan when it fits.
- If the edit needs visuals the user has not added, add a fenced json block with
  {"asset_requests": [{"id", "kind" (image or video), "purpose", "prompt", "aspect"}]} and say
  which box to drop the files in once they make them in an outside tool.
- End with one line: review the plan, then press Execute (or reply with changes).
- The 120-word limit below does not apply to a plan, and a fenced json block is allowed; keep
  the rest scannable."#
            }
            Self::Execute => {
                r#"Current stage: EXECUTE (Claude Sonnet 5.5). Carry out the latest plan and any review notes
from earlier in this conversation exactly, with the user's prompt taking priority. If there is no
plan, make a sensible edit from the prompt.
4. Read get_timeline, then commit_timeline with expected_revision set to the current revision.
   Give every clip a short rationale. Originals are never modified; each commit is a new revision.
5. After committing, call start_render with final=false to make a preview, and poll
   get_job_status until it succeeds or fails.
6. Reply briefly in plain language: what you cut and why, the revision number, and whether the
   preview render succeeded. If you departed from the plan, say where and why."#
            }
            Self::Review => {
                r#"Current stage: REVIEW (Claude Opus 5.5). You check the edit; you do not change it. Tools that
commit or add sounds are switched off in this stage.
- Do steps 1-3, then read get_timeline (and diff_timeline against the previous revision).
- If the current revision has no successful preview, call start_render with final=false and
  poll get_job_status. If a final render of it exists, call run_qa and use the measured results.
- You cannot see or hear renders. Judge from the timeline, transcript words, the brief and
  measured QA only, and say so if a point needs the user's eyes or ears.
- Reply with what works in one line, then numbered notes: each a concrete, fixable change
  (what, where, why). Say "No changes needed" if that is true.
- End with one line: press Execute to apply the notes, or edit them first."#
            }
        }
    }
}

fn editor_instructions(project_id: &str, stage: Stage) -> String {
    let stage_rules = stage.instructions();
    format!(
        r#"You are the video editor inside AgenEditor. The user's project ID is {project_id}.
Work only through the ageneditor MCP tools; you have no other tools.

{stage_rules}

For each request:
1. Call get_brief (the user's standing instructions) and list_assets.
2. Asset roles: primary is the main footage (tracks V1/A1); broll and logo are overlays (V2);
   music goes on A2; reference files are examples of the style or pacing the user wants. Never put
   a reference asset on any track. You can read their metadata, not their pictures or sound, so say
   so if matching them closely matters.
3. If the main footage has speech, call transcribe_asset, then get_transcript, and cut on exact
   words. If transcription says the speech model is missing, tell the user to press
   "Install speech model" and continue without a transcript where you can.
Never claim a render, check or measurement that did not succeed.

Transitions:
- Cuts are hard cuts unless the user asks for transitions or their instructions call for a style
  (e.g. "cinematic", "montage", "smooth").
- Call list_transitions; use only the preset IDs it returns (they come from styles the user ticked
  active). If what they ask for isn't listed, say which style to tick in the "Transitions" panel.
- Put them in the timeline's `transitions` list: {{after_clip_id, preset, duration_frames,
  rationale}}. after_clip_id is the V1 clip before the cut. Start from the suggested_frames.
  A transition never changes the total length; speech audio keeps a hard cut underneath.
- Match the mood: one style per video, used with restraint (not on every cut unless asked).

Sound effects:
- Only use them when the user asks for sound effects or their instructions call for them.
- Find sounds with list_sound_effects (search words like "whoosh", "pop", "click"). It only
  returns sounds from packs the user ticked active. If active_packs is 0 or nothing fits, say they
  can tick a pack active or click "Download & install" on a free pack in the "Sound effects"
  panel, then finish the rest of the edit. You cannot download or install sounds yourself.
- Call add_sound_effect for each sound you use, then place it on track A3 (A3 clips may overlap).
  Keep them short and purposeful, e.g. a whoosh on a B-roll cut or a pop as a caption appears,
  usually with gain_db between -12 and -4 so speech stays clear.
- If a sound you used has a non-empty credit, tell the user the exact credit line to publish.

The user's screen has three import boxes. Name them exactly when telling the user what to add:
- "Main footage" box: files become primary.
- "References" box: files become reference.
- "Assets" box: video and images become broll, audio becomes music.
The user cannot change a file's role after import; to use a file in another role they add it again
in the right box. Never mention asset IDs, track names, roles or tool names unless the user asks.

Reply format (the reply is shown as Markdown):
- Start with one short sentence that says the outcome.
- Then at most a few short bullet points or numbered steps, with a **bold** lead-in where useful.
- No headings, tables, code blocks or emoji. Keep it under about 120 words.

AgenEditor never generates media and neither do you. If the edit needs a file the user has not
added, say exactly what to add (they can make it in an outside tool) and which box to drop it in."#
    )
}

fn is_init(line: &Value) -> bool {
    line.get("type").and_then(Value::as_str) == Some("system")
        && line.get("subtype").and_then(Value::as_str) == Some("init")
}

/// Whether Claude Code's init line reports the AgenEditor MCP server as connected.
pub fn engine_connected(init: &Value) -> bool {
    init.get("mcp_servers")
        .and_then(Value::as_array)
        .is_some_and(|servers| {
            servers.iter().any(|server| {
                server.get("name").and_then(Value::as_str) == Some("ageneditor")
                    && server.get("status").and_then(Value::as_str) == Some("connected")
            })
        })
}

/// Reduces one Claude Code stream-json line to the few events the window shows.
pub fn agent_events(line: &Value) -> Vec<Value> {
    if line.get("type").and_then(Value::as_str) != Some("assistant") {
        return Vec::new();
    }
    let Some(blocks) = line.pointer("/message/content").and_then(Value::as_array) else {
        return Vec::new();
    };
    blocks
        .iter()
        .filter_map(|block| {
            match block.get("type").and_then(Value::as_str) {
            Some("text") => block
                .get("text")
                .and_then(Value::as_str)
                .filter(|text| !text.trim().is_empty())
                .map(|text| json!({"kind": "text", "text": text})),
            Some("tool_use") => block.get("name").and_then(Value::as_str).map(|name| {
                json!({"kind": "tool", "name": name.trim_start_matches("mcp__ageneditor__")})
            }),
            _ => None,
        }
        })
        .collect()
}

pub struct AgentRequest {
    pub workspace: PathBuf,
    pub slot: AgentSlot,
    pub sessions: Sessions,
    pub run: u64,
    pub project_id: String,
    pub prompt: String,
    pub stage: Stage,
}

fn write_config(
    app: &tauri::AppHandle,
    workspace: &Path,
    project_id: &str,
    stage: Stage,
) -> Result<(PathBuf, PathBuf, PathBuf), String> {
    let dir = app
        .path()
        .app_local_data_dir()
        .map_err(|e| e.to_string())?
        .join("agent");
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let mcp_config = dir.join("mcp.json");
    let config = json!({"mcpServers": {"ageneditor": {
        "command": engine_cli(app)?,
        "args": ["mcp", "--workspace", workspace, "--shared-service"],
    }}});
    fs::write(&mcp_config, config.to_string()).map_err(|e| e.to_string())?;
    let instructions = dir.join(format!("instructions_{project_id}_{}.txt", stage.id()));
    fs::write(&instructions, editor_instructions(project_id, stage)).map_err(|e| e.to_string())?;
    Ok((dir, mcp_config, instructions))
}

pub fn run_agent_blocking(app: tauri::AppHandle, request: AgentRequest) -> Result<Value, String> {
    let AgentRequest {
        workspace,
        slot,
        sessions,
        run,
        project_id,
        prompt,
        stage,
    } = request;
    if !valid_project_id(&project_id) {
        return Err("Invalid project".into());
    }
    let prompt = prompt.trim().to_string();
    if prompt.is_empty() {
        return Err("Write a prompt first".into());
    }
    if prompt.chars().count() > MAX_PROMPT_CHARS {
        return Err(format!(
            "Keep the prompt under {MAX_PROMPT_CHARS} characters"
        ));
    }
    let claude = find_claude().ok_or(
        "Claude Code was not found. Install it from claude.com/claude-code and sign in once in a terminal.",
    )?;
    let (dir, mcp_config, instructions) = write_config(&app, &workspace, &project_id, stage)?;
    let resumed = sessions
        .lock()
        .map_err(|_| "Session state unavailable".to_string())?
        .get(&project_id)
        .cloned();

    let mut command = Command::new(claude);
    command
        .current_dir(&dir)
        .args(["-p", "--output-format", "stream-json", "--verbose"])
        .args(["--tools", "", "--strict-mcp-config", "--mcp-config"])
        .arg(&mcp_config)
        .args([
            "--allowedTools",
            "mcp__ageneditor",
            "--append-system-prompt-file",
        ])
        .arg(&instructions)
        .args(["--model", stage.model(), "--effort", stage.effort()])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let blocked = stage.blocked_tools();
    if !blocked.is_empty() {
        command.arg("--disallowedTools").args(&blocked);
    }
    if let Some(session) = &resumed {
        command.args(["--resume", session]);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }

    let (stdin, stdout, stderr) = {
        let mut guard = slot
            .lock()
            .map_err(|_| "Agent state unavailable".to_string())?;
        if guard.is_some() {
            return Err("Claude is already working. Wait for it or press Stop.".into());
        }
        let mut child = command
            .spawn()
            .map_err(|e| format!("Could not start Claude Code: {e}"))?;
        let pipes = (child.stdin.take(), child.stdout.take(), child.stderr.take());
        *guard = Some(RunningAgent {
            run,
            child,
            stopped: None,
        });
        pipes
    };
    // The prompt goes over stdin, so no shell or argument quoting ever touches it.
    if let Some(mut stdin) = stdin {
        let _ = stdin.write_all(prompt.as_bytes());
    }
    let stderr_reader = stderr.map(|mut stderr| {
        thread::spawn(move || {
            let mut text = String::new();
            let _ = stderr.read_to_string(&mut text);
            text
        })
    });
    let watchdog = Arc::clone(&slot);
    thread::spawn(move || {
        let deadline = Instant::now() + AGENT_TIMEOUT;
        loop {
            thread::sleep(std::time::Duration::from_millis(500));
            let Ok(mut guard) = watchdog.lock() else {
                return;
            };
            match guard.as_mut() {
                Some(agent) if agent.run == run => {
                    if Instant::now() >= deadline {
                        agent.stopped = Some("timed out after 30 minutes");
                        let _ = agent.child.kill();
                        return;
                    }
                }
                _ => return,
            }
        }
    });

    let mut session_id: Option<String> = None;
    let mut result: Option<Value> = None;
    if let Some(stdout) = stdout {
        for line in BufReader::new(stdout).lines() {
            let Ok(line) = line else { break };
            let Ok(value) = serde_json::from_str::<Value>(&line) else {
                continue;
            };
            if let Some(id) = value.get("session_id").and_then(Value::as_str) {
                session_id = Some(id.to_string());
            }
            // Without the engine, Claude would improvise fake tool calls; stop instead.
            if is_init(&value) && !engine_connected(&value) {
                if let Ok(mut guard) = slot.lock() {
                    if let Some(agent) = guard.as_mut() {
                        agent.stopped = Some(
                            "couldn't connect to the AgenEditor engine. Restart AgenEditor and try again",
                        );
                        let _ = agent.child.kill();
                    }
                }
                break;
            }
            for event in agent_events(&value) {
                let _ = app.emit(
                    "agent-event",
                    json!({"project_id": project_id, "event": event}),
                );
            }
            if value.get("type").and_then(Value::as_str) == Some("result") {
                result = Some(value);
            }
        }
    }

    let stopped = {
        let mut guard = slot
            .lock()
            .map_err(|_| "Agent state unavailable".to_string())?;
        let mut agent = guard.take().ok_or("Agent state lost")?;
        let _ = agent.child.wait();
        agent.stopped
    };
    let stderr = stderr_reader
        .and_then(|reader| reader.join().ok())
        .unwrap_or_default();
    let failed = result
        .as_ref()
        .and_then(|value| value.get("is_error"))
        .and_then(Value::as_bool)
        .unwrap_or(true);
    if let Ok(mut map) = sessions.lock() {
        match (&session_id, failed && resumed.is_some()) {
            // A broken or expired session must not block the next prompt.
            (_, true) => {
                map.remove(&project_id);
            }
            (Some(id), false) => {
                map.insert(project_id.clone(), id.clone());
            }
            _ => {}
        }
    }
    if let Some(reason) = stopped {
        return Ok(json!({"ok": false, "stopped": reason, "text": null, "stage": stage.id()}));
    }
    let Some(result) = result else {
        let tail: Vec<char> = stderr.trim().chars().collect();
        let tail: String = tail[tail.len().saturating_sub(600)..].iter().collect();
        return Err(if tail.is_empty() {
            "Claude Code stopped without a reply".into()
        } else {
            format!("Claude Code stopped without a reply: {tail}")
        });
    };
    Ok(json!({
        "ok": !failed,
        "stopped": null,
        "text": result.get("result").cloned().unwrap_or(Value::Null),
        "cost_usd": result.get("total_cost_usd").cloned().unwrap_or(Value::Null),
        "stage": stage.id(),
        "model": stage.model(),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_only_text_and_tool_names() {
        let line = json!({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Cutting the intro."},
            {"type": "text", "text": "   "},
            {"type": "tool_use", "name": "mcp__ageneditor__commit_timeline", "input": {"big": "payload"}},
            {"type": "thinking", "thinking": "private"},
        ]}});
        assert_eq!(
            agent_events(&line),
            vec![
                json!({"kind": "text", "text": "Cutting the intro."}),
                json!({"kind": "tool", "name": "commit_timeline"}),
            ]
        );
        assert!(agent_events(&json!({"type": "user", "message": {"content": []}})).is_empty());
    }

    #[test]
    fn detects_a_failed_engine_connection_from_the_init_line() {
        let init = |status: &str| {
            json!({"type": "system", "subtype": "init",
                   "mcp_servers": [{"name": "ageneditor", "status": status}]})
        };
        assert!(is_init(&init("connected")));
        assert!(engine_connected(&init("connected")));
        assert!(!engine_connected(&init("failed")));
        assert!(!engine_connected(
            &json!({"type": "system", "subtype": "init", "mcp_servers": []})
        ));
        assert!(!is_init(&json!({"type": "assistant"})));
    }

    #[test]
    fn stages_pick_model_effort_and_tools() {
        assert_eq!(Stage::parse("plan"), Some(Stage::Plan));
        assert_eq!(Stage::parse("Plan"), None);
        assert_eq!(Stage::Plan.model(), "claude-opus-5-5");
        assert_eq!(Stage::Execute.model(), "claude-sonnet-5-5");
        assert_eq!(Stage::Review.model(), "claude-opus-5-5");
        for stage in [Stage::Plan, Stage::Execute, Stage::Review] {
            assert_eq!(stage.effort(), "high");
            assert_eq!(Stage::parse(stage.id()), Some(stage));
        }
        assert!(Stage::Execute.blocked_tools().is_empty());
        let plan = Stage::Plan.blocked_tools();
        let review = Stage::Review.blocked_tools();
        for tool in ["commit_timeline", "apply_revision", "add_sound_effect"] {
            let name = format!("mcp__ageneditor__{tool}");
            assert!(plan.contains(&name) && review.contains(&name));
        }
        let render = "mcp__ageneditor__start_render".to_string();
        assert!(plan.contains(&render) && !review.contains(&render));
        assert!(editor_instructions("p1", Stage::Review).contains("Current stage: REVIEW"));
        let commit_step = "commit_timeline with expected_revision";
        assert!(editor_instructions("p1", Stage::Execute).contains(commit_step));
        assert!(!editor_instructions("p1", Stage::Plan).contains(commit_step));
    }

    #[test]
    fn rejects_project_ids_that_could_escape_a_file_name() {
        assert!(valid_project_id("project_abc-123"));
        assert!(!valid_project_id("../x"));
        assert!(!valid_project_id(""));
        assert!(!valid_project_id("a b"));
    }
}
