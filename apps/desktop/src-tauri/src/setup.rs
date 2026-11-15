//! First-run setup on a new PC. AgenEditor bundles its engine and FFmpeg, so the only
//! outside requirement is Claude Code, installed and signed in. These commands check it,
//! run Anthropic's official installer when the person clicks Install, and open the sign-in.

use serde_json::{json, Value};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant, SystemTime};

use crate::agent::find_claude;

/// Anthropic's documented Windows installer (code.claude.com/docs/en/setup).
pub const INSTALLER_URL: &str = "https://claude.ai/install.ps1";
const CHECK_TIMEOUT: Duration = Duration::from_secs(30);
const INSTALL_TIMEOUT: Duration = Duration::from_secs(15 * 60);
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const CREATE_NEW_CONSOLE: u32 = 0x0000_0010;

/// Runs a command without a window and returns (success, stdout, stderr), or None on timeout.
fn run(mut command: Command, timeout: Duration) -> Option<(bool, String, String)> {
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = command.spawn().ok()?;
    let reader = |pipe: Option<Box<dyn Read + Send>>| {
        thread::spawn(move || {
            let mut text = String::new();
            if let Some(mut pipe) = pipe {
                let _ = pipe.read_to_string(&mut text);
            }
            text
        })
    };
    let out = reader(
        child
            .stdout
            .take()
            .map(|p| Box::new(p) as Box<dyn Read + Send>),
    );
    let err = reader(
        child
            .stderr
            .take()
            .map(|p| Box::new(p) as Box<dyn Read + Send>),
    );
    let deadline = Instant::now() + timeout;
    let status = loop {
        if let Ok(Some(status)) = child.try_wait() {
            break status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return None;
        }
        thread::sleep(Duration::from_millis(200));
    };
    Some((
        status.success(),
        out.join().unwrap_or_default(),
        err.join().unwrap_or_default(),
    ))
}

fn tail(text: &str, chars: usize) -> String {
    let all: Vec<char> = text.trim().chars().collect();
    all[all.len().saturating_sub(chars)..].iter().collect()
}

/// Reduces `claude auth status --json` to what the setup screen shows. The account email
/// and organisation are left out on purpose.
pub fn auth_summary(status: &Value) -> Value {
    json!({
        "signed_in": status.get("loggedIn").and_then(Value::as_bool).unwrap_or(false),
        "method": status.get("authMethod").cloned().unwrap_or(Value::Null),
        "plan": status.get("subscriptionType").cloned().unwrap_or(Value::Null),
    })
}

/// Whether Claude Code is installed and signed in on this PC.
pub fn claude_status_blocking() -> Value {
    let claude = match trusted_claude() {
        Ok(path) => path,
        Err(problem) => {
            // Not found is a plain "not installed"; found-but-untrusted explains why it is refused.
            let untrusted = find_claude().is_some();
            return json!({
                "installed": false, "version": null, "signed_in": false, "plan": null, "method": null,
                "problem": if untrusted { Value::String(problem) } else { Value::Null },
            });
        }
    };
    let mut version_cmd = Command::new(&claude);
    version_cmd.arg("--version");
    let version = run(version_cmd, CHECK_TIMEOUT)
        .filter(|(ok, _, _)| *ok)
        .map(|(_, out, _)| out.trim().to_string())
        .filter(|v| !v.is_empty());
    let mut auth_cmd = Command::new(&claude);
    auth_cmd.args(["auth", "status", "--json"]);
    let auth = run(auth_cmd, CHECK_TIMEOUT)
        .and_then(|(_, out, _)| serde_json::from_str::<Value>(out.trim()).ok())
        .map(|status| auth_summary(&status))
        .unwrap_or_else(|| json!({"signed_in": false, "plan": null, "method": null}));
    json!({
        "installed": version.is_some(),
        "problem": null,
        "version": version,
        "signed_in": auth["signed_in"],
        "plan": auth["plan"],
        "method": auth["method"],
    })
}

/// The signer name on Anthropic's Windows binaries (exact match; a name that merely contains
/// "Anthropic" does not count).
pub const ANTHROPIC_SIGNER: &str = "Anthropic, PBC";

/// Checks the file carries a valid Authenticode signature from Anthropic, PBC.
fn signed_by_anthropic(path: &Path) -> bool {
    let mut command = Command::new("powershell.exe");
    command
        .args(["-NoProfile", "-NonInteractive", "-Command"])
        .arg(
            "$s = Get-AuthenticodeSignature -LiteralPath $env:AGENEDITOR_CHECK_PATH; \
             if ($s.Status -eq 'Valid' -and $s.SignerCertificate -and \
                 $s.SignerCertificate.GetNameInfo('SimpleName', $false) -ceq $env:AGENEDITOR_SIGNER) { 'ok' }",
        )
        .env("AGENEDITOR_CHECK_PATH", path)
        .env("AGENEDITOR_SIGNER", ANTHROPIC_SIGNER);
    run(command, CHECK_TIMEOUT).is_some_and(|(_, out, _)| out.trim() == "ok")
}

type Fingerprint = (PathBuf, u64, Option<SystemTime>);
static VERIFIED: OnceLock<Mutex<Option<Fingerprint>>> = OnceLock::new();

fn fingerprint(path: &Path) -> Option<Fingerprint> {
    let meta = std::fs::metadata(path).ok()?;
    Some((path.to_path_buf(), meta.len(), meta.modified().ok()))
}

/// The Claude Code executable AgenEditor is willing to run: found on this PC and signed by
/// Anthropic, PBC. Every run, polish, usage check and status read goes through here. The (slow)
/// signature check runs once per file; it repeats if the file's size or timestamp changes.
pub fn trusted_claude() -> Result<PathBuf, String> {
    let path = find_claude().ok_or(
        "Claude Code was not found. Open Home and use Set up this PC to install it and sign in.",
    )?;
    let print = fingerprint(&path);
    let cell = VERIFIED.get_or_init(|| Mutex::new(None));
    if let (Some(print), Ok(guard)) = (&print, cell.lock()) {
        if guard.as_ref() == Some(print) {
            return Ok(path);
        }
    }
    if !signed_by_anthropic(&path) {
        return Err(format!(
            "Claude Code was found at {} but its digital signature could not be confirmed as Anthropic's, so AgenEditor will not run it.",
            path.display()
        ));
    }
    if let (Some(print), Ok(mut guard)) = (print, cell.lock()) {
        *guard = Some(print);
    }
    Ok(path)
}

/// Runs Anthropic's official installer, then verifies the result. Only the person's click
/// starts this; the URL is fixed.
pub fn install_claude_code_blocking() -> Result<Value, String> {
    if find_claude().is_none() {
        let mut command = Command::new("powershell.exe");
        command.args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            &format!("irm {INSTALLER_URL} | iex"),
        ]);
        match run(command, INSTALL_TIMEOUT) {
            None => return Err("The Claude Code installer timed out after 15 minutes".into()),
            Some((false, out, err)) => {
                let detail = tail(if err.trim().is_empty() { &out } else { &err }, 500);
                return Err(format!(
                    "The Claude Code installer failed. Check your internet connection and try again.{}",
                    if detail.is_empty() { String::new() } else { format!(" Details: {detail}") }
                ));
            }
            Some((true, _, _)) => {}
        }
    }
    if find_claude().is_none() {
        return Err("The installer finished but Claude Code was not found. Restart AgenEditor and check again.".into());
    }
    trusted_claude()?;
    Ok(claude_status_blocking())
}

/// Opens Claude Code's sign-in in its own console window (it continues in the browser).
pub fn open_claude_sign_in() -> Result<(), String> {
    let claude = trusted_claude()?;
    let mut command = Command::new(claude);
    command.args(["auth", "login"]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NEW_CONSOLE);
    }
    command
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("Could not open the Claude sign-in: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn auth_summary_keeps_status_and_plan_but_drops_personal_details() {
        let summary = auth_summary(&json!({
            "loggedIn": true, "authMethod": "claude.ai", "subscriptionType": "team",
            "email": "someone@example.com", "orgName": "Example Inc.",
        }));
        assert_eq!(
            summary,
            json!({"signed_in": true, "method": "claude.ai", "plan": "team"})
        );
        assert_eq!(auth_summary(&json!({}))["signed_in"], json!(false));
    }

    #[test]
    fn installer_url_is_anthropics_https_script() {
        assert_eq!(INSTALLER_URL, "https://claude.ai/install.ps1");
    }
}
