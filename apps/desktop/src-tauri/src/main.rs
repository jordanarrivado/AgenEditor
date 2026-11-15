#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod agent;
mod polish;
mod setup;
mod usage;

use reqwest::blocking::Client;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{DragDropEvent, Emitter, Manager, State, WindowEvent};
use tauri_plugin_dialog::DialogExt;

/// How long a dropped file stays importable.
const DROP_TTL: Duration = Duration::from_secs(60);
/// Roles the shell may import a file as.
const IMPORT_ROLES: &[&str] = &["primary", "broll", "music", "logo", "reference"];
/// Most files one picker selection or drop may import.
const MAX_IMPORT_FILES: usize = 50;
/// Audio-only extensions that the Assets box imports as music.
const AUDIO_EXTENSIONS: &[&str] = &["mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "wma"];
/// Longest a single Claude editing run may take before the shell stops it.
const AGENT_TIMEOUT: Duration = Duration::from_secs(30 * 60);
/// Longest prompt the shell forwards to Claude.
const MAX_PROMPT_CHARS: usize = 20_000;

type DropSlot = Arc<Mutex<Option<(Instant, Vec<PathBuf>)>>>;

/// The Claude Code process currently editing, if any. Only one runs at a time.
struct RunningAgent {
    run: u64,
    child: Child,
    stopped: Option<&'static str>,
}

type AgentSlot = Arc<Mutex<Option<RunningAgent>>>;
type Sessions = Arc<Mutex<HashMap<String, String>>>;

struct EngineState {
    workspace: PathBuf,
    _child: Mutex<Option<Child>>,
    /// Paths from the latest OS file drop. Set only by the window event handler.
    dropped: DropSlot,
    agent: AgentSlot,
    /// Claude Code session per project, so follow-up prompts continue one conversation.
    sessions: Sessions,
    runs: Mutex<u64>,
    /// Latest Claude plan usage seen in a run (see usage.rs).
    usage: usage::UsageSlot,
}

#[derive(serde::Deserialize)]
struct Discovery {
    port: u16,
    token: String,
}

fn discovery(workspace: &Path) -> Result<Discovery, String> {
    let canonical = workspace.canonicalize().map_err(|e| e.to_string())?;
    let normalized = canonical
        .to_string_lossy()
        .trim_start_matches(r"\\?\")
        .replace('\\', "/")
        .to_lowercase();
    let key = format!("{:x}", Sha256::digest(normalized.as_bytes()));
    let base =
        std::env::var_os("LOCALAPPDATA").ok_or("Local application directory is unavailable")?;
    let path = PathBuf::from(base)
        .join("AgenEditor/services")
        .join(key)
        .join("service.json");
    let raw = fs::read_to_string(path)
        .map_err(|_| "Engine service is starting or unavailable".to_string())?;
    let info: Discovery =
        serde_json::from_str(&raw).map_err(|_| "Invalid engine service state".to_string())?;
    if info.token.len() != 64 || info.port == 0 {
        return Err("Invalid engine service credentials".into());
    }
    Ok(info)
}

fn request(workspace: &Path, endpoint: &str, payload: Value) -> Result<Value, String> {
    request_with_timeout(workspace, endpoint, payload, Duration::from_secs(120))
}

fn request_with_timeout(
    workspace: &Path,
    endpoint: &str,
    payload: Value,
    timeout: Duration,
) -> Result<Value, String> {
    if endpoint != "/call" && endpoint != "/desktop" {
        return Err("Invalid engine endpoint".into());
    }
    let info = discovery(workspace)?;
    let response = Client::builder()
        .no_proxy()
        .timeout(timeout)
        .build()
        .map_err(|_| "Could not initialize engine connection".to_string())?
        .post(format!("http://127.0.0.1:{}{}", info.port, endpoint))
        .bearer_auth(info.token)
        .json(&payload)
        .send()
        .map_err(|_| "Engine service is unavailable".to_string())?;
    let status = response.status();
    let body: Value = response
        .json()
        .map_err(|_| "Invalid engine response".to_string())?;
    if !status.is_success() {
        return Err(body
            .get("error")
            .map(Value::to_string)
            .unwrap_or_else(|| "Engine request failed".into()));
    }
    Ok(body)
}

fn service_ready(workspace: &Path) -> bool {
    let Ok(info) = discovery(workspace) else {
        return false;
    };
    Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(1))
        .build()
        .ok()
        .and_then(|client| {
            client
                .get(format!("http://127.0.0.1:{}/health", info.port))
                .bearer_auth(info.token)
                .send()
                .ok()
        })
        .is_some_and(|response| response.status().is_success())
}

fn start_service(app: &tauri::AppHandle, workspace: &Path) -> Result<Option<Child>, String> {
    if service_ready(workspace) {
        return Ok(None);
    }
    let mut command;
    if cfg!(debug_assertions) {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let python = root.join(".venv/Scripts/python.exe");
        if !python.is_file() {
            return Err("Run uv sync before tauri dev".into());
        }
        command = Command::new(python);
        command.args(["-m", "ageneditor.cli", "service", "--workspace"]);
        command.arg(workspace);
    } else {
        let binary = app
            .path()
            .resource_dir()
            .map_err(|e| e.to_string())?
            .join("ageneditor-engine.exe");
        if !binary.is_file() {
            return Err("Bundled engine sidecar is missing".into());
        }
        command = Command::new(binary);
        command.args(["service", "--workspace"]);
        command.arg(workspace);
    }
    let ffmpeg_dir = if cfg!(debug_assertions) {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries")
    } else {
        app.path()
            .resource_dir()
            .map_err(|e| e.to_string())?
            .join("binaries")
    };
    let existing_path = std::env::var_os("PATH").unwrap_or_default();
    let mut paths = vec![ffmpeg_dir];
    paths.extend(std::env::split_paths(&existing_path));
    command.env(
        "PATH",
        std::env::join_paths(paths).map_err(|e| e.to_string())?,
    );
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    let mut child = command
        .spawn()
        .map_err(|e| format!("Could not start engine sidecar: {e}"))?;
    for _ in 0..600 {
        if service_ready(workspace) {
            return Ok(Some(child));
        }
        if child.try_wait().map_err(|e| e.to_string())?.is_some() {
            return Err("Engine sidecar exited during startup".into());
        }
        thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
    Err("Engine sidecar did not become ready".into())
}

fn engine_tool_blocking(workspace: PathBuf, tool: String, args: Value) -> Result<Value, String> {
    let result = request(&workspace, "/call", json!({"tool": tool, "args": args}))?;
    if result
        .get("isError")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return Err(result
            .get("content")
            .map(Value::to_string)
            .unwrap_or_else(|| "Engine tool failed".into()));
    }
    Ok(result
        .get("structuredContent")
        .cloned()
        .unwrap_or(Value::Null))
}

fn engine_desktop_blocking(
    workspace: PathBuf,
    operation: String,
    args: Value,
) -> Result<Value, String> {
    const ALLOWED: &[&str] = &[
        "list_projects",
        "create_project",
        "project_summary",
        "get_asset_checklist",
        "get_brief",
        "save_brief",
        "save_asset_checklist",
        "link_asset_request",
        "approve_asset_request",
        "replace_asset_request_link",
        "reset_asset_request",
        "accept_asset_issue",
        "list_jobs",
        "list_revisions",
        "list_transcripts",
        "source_range",
        "asset_path",
        "render_path",
        "remove_asset",
        "speech_model_status",
        "list_sound_packs",
        "list_sound_catalog",
        "set_active_sound_pack",
        "remove_sound_pack",
        "list_transition_styles",
        "set_active_transition_style",
    ];
    if !ALLOWED.contains(&operation.as_str()) {
        return Err("Desktop operation is not allowed".into());
    }
    let result = request(
        &workspace,
        "/desktop",
        json!({"operation": operation, "args": args}),
    )?;
    Ok(result.get("result").cloned().unwrap_or(Value::Null))
}

/// Installs a sound pack from a folder the person picks. The path never comes from the page.
fn install_sound_pack_blocking(
    app: tauri::AppHandle,
    workspace: PathBuf,
    details: Value,
) -> Result<Option<Value>, String> {
    let Some(selected) = app.dialog().file().blocking_pick_folder() else {
        return Ok(None);
    };
    let folder = selected
        .into_path()
        .map_err(|_| "Selected item is not a local folder".to_string())?;
    let field = |name: &str| {
        details
            .get(name)
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string()
    };
    // Each of up to 500 sounds is probed and hashed, so allow more than a normal request.
    let result = request_with_timeout(
        &workspace,
        "/desktop",
        json!({"operation": "install_sound_pack", "args": {
            "source_path": folder,
            "name": field("name"),
            "license": field("license"),
            "credit": field("credit"),
            "source": field("source"),
        }}),
        Duration::from_secs(15 * 60),
    )?;
    Ok(result.get("result").cloned())
}

/// Installs a pack from AgenEditor's fixed CC0 catalog. The engine resolves the ID to a pinned
/// HTTPS URL and SHA-256 (or the copy bundled with the app); the page never supplies a URL.
fn download_sound_pack_blocking(workspace: PathBuf, catalog_id: String) -> Result<Value, String> {
    let valid = (2..=64).contains(&catalog_id.len())
        && catalog_id
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-');
    if !valid {
        return Err("Unknown sound pack".into());
    }
    let result = request_with_timeout(
        &workspace,
        "/desktop",
        json!({"operation": "download_sound_pack", "args": {"catalog_id": catalog_id}}),
        Duration::from_secs(15 * 60),
    )?;
    result
        .get("result")
        .cloned()
        .ok_or_else(|| "Engine did not return the installed pack".to_string())
}

fn import_media_blocking(
    app: tauri::AppHandle,
    workspace: PathBuf,
    project_id: String,
    role: String,
    tool_name: Option<String>,
) -> Result<Option<Value>, String> {
    if !IMPORT_ROLES.contains(&role.as_str()) {
        return Err("Invalid media role".into());
    }
    let Some(selected) = app.dialog().file().blocking_pick_file() else {
        return Ok(None);
    };
    let path = selected
        .into_path()
        .map_err(|_| "Selected item is not a local file".to_string())?;
    import_path_blocking(&workspace, project_id, role, tool_name, path).map(Some)
}

fn import_path_blocking(
    workspace: &Path,
    project_id: String,
    role: String,
    tool_name: Option<String>,
    path: PathBuf,
) -> Result<Value, String> {
    if !IMPORT_ROLES.contains(&role.as_str()) {
        return Err("Invalid media role".into());
    }
    if !path.is_file() {
        return Err("Selected item is not a file".into());
    }
    let provenance = match tool_name {
        Some(name) => json!({"origin": "generated-external", "tool_name": name}),
        None => json!({"origin": "original"}),
    };
    let result = request(
        workspace,
        "/desktop",
        json!({"operation": "import_file", "args": {
            "project_id": project_id, "source_path": path, "role": role, "provenance": provenance
        }}),
    )?;
    result
        .get("result")
        .cloned()
        .ok_or_else(|| "Engine did not return the imported asset".to_string())
}

/// Imports the file the user most recently dropped on the window. The path never
/// comes from the web page: only the OS drop event handled in `main` can set it.
fn import_dropped_blocking(
    workspace: PathBuf,
    drops: &Mutex<Option<(Instant, Vec<PathBuf>)>>,
    project_id: String,
    role: String,
    tool_name: Option<String>,
) -> Result<Value, String> {
    if !IMPORT_ROLES.contains(&role.as_str()) {
        return Err("Invalid media role".into());
    }
    let dropped = drops
        .lock()
        .map_err(|_| "Drop state unavailable".to_string())?
        .take();
    let Some((when, paths)) = dropped else {
        return Err("No dropped file is waiting to be imported".into());
    };
    if when.elapsed() > DROP_TTL {
        return Err("The dropped file expired; drop it again".into());
    }
    let [path] = <[PathBuf; 1]>::try_from(paths)
        .map_err(|_| "Drop exactly one file on a request".to_string())?;
    import_path_blocking(&workspace, project_id, role, tool_name, path)
}

/// The role a file gets from the box it was added to: main footage, references or assets.
fn bucket_role(bucket: &str, path: &Path) -> Result<&'static str, String> {
    match bucket {
        "main" => Ok("primary"),
        "reference" => Ok("reference"),
        "asset" => {
            let extension = path
                .extension()
                .map(|e| e.to_string_lossy().to_lowercase())
                .unwrap_or_default();
            Ok(if AUDIO_EXTENSIONS.contains(&extension.as_str()) {
                "music"
            } else {
                "broll"
            })
        }
        _ => Err("Unknown import box".into()),
    }
}

/// Imports each file independently; one bad file does not stop the others.
fn import_bucket_paths(
    workspace: &Path,
    project_id: &str,
    bucket: &str,
    tool_name: Option<String>,
    paths: Vec<PathBuf>,
) -> Result<Vec<Value>, String> {
    if paths.len() > MAX_IMPORT_FILES {
        return Err(format!("Add at most {MAX_IMPORT_FILES} files at once"));
    }
    let tool_name = tool_name
        .map(|name| name.trim().to_string())
        .filter(|name| !name.is_empty());
    Ok(paths
        .into_iter()
        .map(|path| {
            let file = path
                .file_name()
                .map(|name| name.to_string_lossy().into_owned())
                .unwrap_or_default();
            let outcome = bucket_role(bucket, &path).and_then(|role| {
                import_path_blocking(
                    workspace,
                    project_id.to_string(),
                    role.to_string(),
                    tool_name.clone(),
                    path,
                )
            });
            match outcome {
                Ok(asset) => json!({"file": file, "asset": asset}),
                Err(error) => json!({"file": file, "error": error}),
            }
        })
        .collect())
}

fn import_files_blocking(
    app: tauri::AppHandle,
    workspace: PathBuf,
    project_id: String,
    bucket: String,
    tool_name: Option<String>,
) -> Result<Option<Vec<Value>>, String> {
    let Some(selected) = app.dialog().file().blocking_pick_files() else {
        return Ok(None);
    };
    let paths = selected
        .into_iter()
        .map(|item| item.into_path())
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| "Selected item is not a local file".to_string())?;
    import_bucket_paths(&workspace, &project_id, &bucket, tool_name, paths).map(Some)
}

/// Imports every file from the latest OS drop into one box. Paths never come from the page.
fn import_dropped_files_blocking(
    workspace: PathBuf,
    drops: &Mutex<Option<(Instant, Vec<PathBuf>)>>,
    project_id: String,
    bucket: String,
    tool_name: Option<String>,
) -> Result<Vec<Value>, String> {
    let dropped = drops
        .lock()
        .map_err(|_| "Drop state unavailable".to_string())?
        .take();
    let Some((when, paths)) = dropped else {
        return Err("No dropped files are waiting to be imported".into());
    };
    if when.elapsed() > DROP_TTL {
        return Err("The dropped files expired; drop them again".into());
    }
    import_bucket_paths(&workspace, &project_id, &bucket, tool_name, paths)
}

/// The engine CLI that also serves MCP over stdio.
fn engine_cli(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    if cfg!(debug_assertions) {
        Ok(PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../..")
            .join(".venv/Scripts/ageneditor.exe"))
    } else {
        Ok(app
            .path()
            .resource_dir()
            .map_err(|e| e.to_string())?
            .join("ageneditor-engine.exe"))
    }
}

fn export_media_blocking(
    app: tauri::AppHandle,
    workspace: PathBuf,
    project_id: String,
    job_id: String,
) -> Result<Option<String>, String> {
    let result = request(
        &workspace,
        "/desktop",
        json!({"operation": "render_path", "args": {
            "project_id": project_id, "job_id": job_id, "require_qa": true
        }}),
    )?;
    let source = result
        .get("result")
        .and_then(|value| value.get("path"))
        .and_then(Value::as_str)
        .ok_or("Engine did not return an export path")?;
    let Some(selected) = app
        .dialog()
        .file()
        .add_filter("MP4 video", &["mp4"])
        .blocking_save_file()
    else {
        return Ok(None);
    };
    let mut destination = selected
        .into_path()
        .map_err(|_| "Choose a local export path".to_string())?;
    destination.set_extension("mp4");
    let parent = destination
        .parent()
        .ok_or("Choose an export directory")?
        .canonicalize()
        .map_err(|e| e.to_string())?;
    let destination = parent.join(destination.file_name().ok_or("Choose an export filename")?);
    if destination.starts_with(workspace.canonicalize().map_err(|e| e.to_string())?) {
        return Err("Export outside the project workspace to preserve originals".into());
    }
    let mut input = fs::File::open(source).map_err(|e| e.to_string())?;
    let mut output = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&destination)
        .map_err(|e| format!("Choose a new filename; could not create export: {e}"))?;
    if let Err(error) = std::io::copy(&mut input, &mut output).and_then(|_| output.sync_all()) {
        drop(output);
        let _ = fs::remove_file(&destination);
        return Err(format!("Could not complete export: {error}"));
    }
    Ok(Some(destination.to_string_lossy().into_owned()))
}

fn engine_connection_blocking(app: tauri::AppHandle, workspace: PathBuf) -> Result<Value, String> {
    let connected = service_ready(&workspace);
    let workspace = workspace.to_string_lossy();
    let command = format!(
        "\"{}\" mcp --workspace \"{}\" --shared-service",
        engine_cli(&app)?.display(),
        workspace
    );
    Ok(json!({
        "workspace": workspace,
        "command": command,
        "connected": connected,
        "claude_available": agent::find_claude().is_some(),
    }))
}

fn cache_model_blocking(app: tauri::AppHandle, preset: String) -> Result<String, String> {
    if !["tiny", "base", "small"].contains(&preset.as_str()) {
        return Err("Invalid model preset".into());
    }
    let mut command;
    if cfg!(debug_assertions) {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
        command = Command::new(root.join(".venv/Scripts/ageneditor.exe"));
    } else {
        command = Command::new(
            app.path()
                .resource_dir()
                .map_err(|e| e.to_string())?
                .join("ageneditor-engine.exe"),
        );
    }
    command
        .args(["cache-model", &preset])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command
        .spawn()
        .map_err(|e| format!("Could not start model download: {e}"))?;
    for _ in 0..9000 {
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            return if status.success() {
                Ok(format!("{preset} model cached"))
            } else {
                Err("Model download failed; check connectivity and disk space".into())
            };
        }
        thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
    Err("Model download timed out".into())
}

async fn blocking<T: Send + 'static>(
    task: impl FnOnce() -> Result<T, String> + Send + 'static,
) -> Result<T, String> {
    tauri::async_runtime::spawn_blocking(task)
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn engine_tool(
    state: State<'_, EngineState>,
    tool: String,
    args: Value,
) -> Result<Value, String> {
    let workspace = state.workspace.clone();
    blocking(move || engine_tool_blocking(workspace, tool, args)).await
}

#[tauri::command]
async fn engine_desktop(
    state: State<'_, EngineState>,
    operation: String,
    args: Value,
) -> Result<Value, String> {
    let workspace = state.workspace.clone();
    blocking(move || engine_desktop_blocking(workspace, operation, args)).await
}

#[tauri::command]
async fn import_media(
    app: tauri::AppHandle,
    state: State<'_, EngineState>,
    project_id: String,
    role: String,
    tool_name: Option<String>,
) -> Result<Option<Value>, String> {
    let workspace = state.workspace.clone();
    blocking(move || import_media_blocking(app, workspace, project_id, role, tool_name)).await
}

#[tauri::command]
async fn import_dropped_media(
    state: State<'_, EngineState>,
    project_id: String,
    role: String,
    tool_name: Option<String>,
) -> Result<Value, String> {
    let workspace = state.workspace.clone();
    let drops = Arc::clone(&state.dropped);
    blocking(move || import_dropped_blocking(workspace, &drops, project_id, role, tool_name)).await
}

#[tauri::command]
async fn import_files(
    app: tauri::AppHandle,
    state: State<'_, EngineState>,
    project_id: String,
    bucket: String,
    tool_name: Option<String>,
) -> Result<Option<Vec<Value>>, String> {
    let workspace = state.workspace.clone();
    blocking(move || import_files_blocking(app, workspace, project_id, bucket, tool_name)).await
}

#[tauri::command]
async fn import_dropped_files(
    state: State<'_, EngineState>,
    project_id: String,
    bucket: String,
    tool_name: Option<String>,
) -> Result<Vec<Value>, String> {
    let workspace = state.workspace.clone();
    let drops = Arc::clone(&state.dropped);
    blocking(move || {
        import_dropped_files_blocking(workspace, &drops, project_id, bucket, tool_name)
    })
    .await
}

#[tauri::command]
async fn install_sound_pack(
    app: tauri::AppHandle,
    state: State<'_, EngineState>,
    details: Value,
) -> Result<Option<Value>, String> {
    let workspace = state.workspace.clone();
    blocking(move || install_sound_pack_blocking(app, workspace, details)).await
}

#[tauri::command]
async fn download_sound_pack(
    state: State<'_, EngineState>,
    catalog_id: String,
) -> Result<Value, String> {
    let workspace = state.workspace.clone();
    blocking(move || download_sound_pack_blocking(workspace, catalog_id)).await
}

#[tauri::command]
fn claude_usage(app: tauri::AppHandle, state: State<'_, EngineState>) -> Value {
    usage::current(&app, &state.usage)
}

#[tauri::command]
async fn refresh_claude_usage(
    app: tauri::AppHandle,
    state: State<'_, EngineState>,
) -> Result<Value, String> {
    let slot = Arc::clone(&state.usage);
    blocking(move || usage::refresh_blocking(app, slot)).await
}

/// The only sites the shell opens for B-roll (spec §3.1). The page picks a name, never a URL.
fn generator_url(site: &str) -> Option<&'static str> {
    match site {
        "chatgpt" => Some("https://chatgpt.com/"),
        "flow" => Some("https://flow.google.com/"),
        _ => None,
    }
}

#[tauri::command]
fn open_generator(site: String) -> Result<(), String> {
    let url = generator_url(&site).ok_or("Unknown site")?;
    let mut command = Command::new("rundll32.exe");
    command.args(["url.dll,FileProtocolHandler", url]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    command
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("Could not open the browser: {e}"))
}

#[tauri::command]
async fn claude_status() -> Result<Value, String> {
    blocking(|| Ok(setup::claude_status_blocking())).await
}

#[tauri::command]
async fn install_claude_code() -> Result<Value, String> {
    blocking(setup::install_claude_code_blocking).await
}

#[tauri::command]
async fn open_claude_sign_in() -> Result<(), String> {
    blocking(setup::open_claude_sign_in).await
}

#[tauri::command]
async fn polish_instructions(app: tauri::AppHandle, text: String) -> Result<String, String> {
    blocking(move || polish::polish_instructions_blocking(app, text)).await
}

#[tauri::command]
async fn run_agent(
    app: tauri::AppHandle,
    state: State<'_, EngineState>,
    project_id: String,
    prompt: String,
    stage: String,
) -> Result<Value, String> {
    let stage = agent::Stage::parse(&stage).ok_or("Unknown stage; pick Plan, Execute or Review")?;
    let run = {
        let mut runs = state
            .runs
            .lock()
            .map_err(|_| "Agent state unavailable".to_string())?;
        *runs += 1;
        *runs
    };
    let request = agent::AgentRequest {
        workspace: state.workspace.clone(),
        slot: Arc::clone(&state.agent),
        sessions: Arc::clone(&state.sessions),
        run,
        project_id,
        prompt,
        stage,
        usage: Arc::clone(&state.usage),
    };
    blocking(move || agent::run_agent_blocking(app, request)).await
}

#[tauri::command]
fn stop_agent(state: State<'_, EngineState>) -> Result<bool, String> {
    let mut guard = state
        .agent
        .lock()
        .map_err(|_| "Agent state unavailable".to_string())?;
    Ok(match guard.as_mut() {
        Some(agent) => {
            agent.stopped = Some("stopped by you");
            let _ = agent.child.kill();
            true
        }
        None => false,
    })
}

#[tauri::command]
fn new_agent_conversation(state: State<'_, EngineState>, project_id: String) -> Result<(), String> {
    state
        .sessions
        .lock()
        .map_err(|_| "Session state unavailable".to_string())?
        .remove(&project_id);
    Ok(())
}

#[tauri::command]
async fn export_media(
    app: tauri::AppHandle,
    state: State<'_, EngineState>,
    project_id: String,
    job_id: String,
) -> Result<Option<String>, String> {
    let workspace = state.workspace.clone();
    blocking(move || export_media_blocking(app, workspace, project_id, job_id)).await
}

#[tauri::command]
async fn engine_connection(
    app: tauri::AppHandle,
    state: State<'_, EngineState>,
) -> Result<Value, String> {
    let workspace = state.workspace.clone();
    blocking(move || engine_connection_blocking(app, workspace)).await
}

#[tauri::command]
async fn cache_model(app: tauri::AppHandle, preset: String) -> Result<String, String> {
    blocking(move || cache_model_blocking(app, preset)).await
}

#[cfg(test)]
mod tests {
    use super::generator_url;

    #[test]
    fn only_chatgpt_and_flow_can_be_opened() {
        assert_eq!(generator_url("chatgpt"), Some("https://chatgpt.com/"));
        assert_eq!(generator_url("flow"), Some("https://flow.google.com/"));
        for other in [
            "",
            "gemini",
            "https://evil.example/",
            "file:///c:/windows",
            "chatgpt.com",
        ] {
            assert_eq!(generator_url(other), None);
        }
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let workspace = app.path().app_local_data_dir()?.join("projects");
            fs::create_dir_all(&workspace)?;
            let child = start_service(app.handle(), &workspace).map_err(std::io::Error::other)?;
            app.manage(EngineState {
                workspace,
                _child: Mutex::new(child),
                dropped: Arc::new(Mutex::new(None)),
                agent: Arc::new(Mutex::new(None)),
                sessions: Arc::new(Mutex::new(HashMap::new())),
                runs: Mutex::new(0),
                usage: Arc::new(Mutex::new(None)),
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::DragDrop(DragDropEvent::Drop { paths, position }) = event {
                if let Some(state) = window.try_state::<EngineState>() {
                    if let Ok(mut slot) = state.dropped.lock() {
                        *slot = Some((Instant::now(), paths.clone()));
                    }
                    let _ = window.emit(
                        "media-dropped",
                        json!({"count": paths.len(), "x": position.x, "y": position.y}),
                    );
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            engine_tool,
            engine_desktop,
            import_media,
            import_dropped_media,
            import_files,
            import_dropped_files,
            install_sound_pack,
            download_sound_pack,
            polish_instructions,
            claude_status,
            open_generator,
            claude_usage,
            refresh_claude_usage,
            install_claude_code,
            open_claude_sign_in,
            run_agent,
            stop_agent,
            new_agent_conversation,
            export_media,
            engine_connection,
            cache_model
        ])
        .run(tauri::generate_context!())
        .expect("AgenEditor desktop failed to start");
}
