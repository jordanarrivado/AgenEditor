# Installing AgenEditor on another Windows PC

## What to share

One file: `apps/desktop/src-tauri/target/release/bundle/nsis/AgenEditor_0.1.0_x64-setup.exe`
(built with `npm run desktop:build`, about 100 MB).

The installer already contains everything AgenEditor runs on:

| Part | How it gets there |
|---|---|
| Desktop app (UI) | Built into the installer |
| Video engine (Python) | Bundled as `ageneditor-engine.exe`, with its own Python runtime and Visual C++ DLLs |
| FFmpeg / FFprobe | Bundled |
| Caption fonts, starter sound pack | Bundled |
| Microsoft WebView2 | Already part of Windows 11; on Windows 10 the installer fetches it if missing |

**Python, Node.js, Git and FFmpeg do not need to be installed.**

## Steps for the person installing

1. Run `AgenEditor_0.1.0_x64-setup.exe`. No administrator rights are needed; it installs for the current
   Windows user. The installer is not code-signed yet, so Windows SmartScreen may show "Windows protected your
   PC": choose **More info → Run anyway**.
2. Open AgenEditor. Home shows **Set up this PC**:
   - **AgenEditor engine** — built in; turns green once it has started.
   - **Claude Code** — click **Install Claude Code**. This runs Anthropic's official installer
     (`https://claude.ai/install.ps1`) for the current user, then checks that `claude.exe` carries a valid
     "Anthropic, PBC" signature. It can take a few minutes.
   - **Claude account** — click **Sign in**; a window opens and sign-in continues in the browser. Claude Code
     needs a Claude Pro, Max, Team or Enterprise plan, or an Anthropic Console account. The checklist turns green
     on its own when sign-in finishes.
   - **Speech model** (optional) — click **Install** to download the ~75 MB model used to cut on spoken words.
3. When the card says **This PC is ready**, create a project.

Internet is needed for the Claude Code install, sign-in, the speech model, free sound pack downloads, and every
Claude run. Video files are never uploaded.

## Known limits

- Windows 10 (1809 or later) or Windows 11, x64.
- Unsigned installer: expect the SmartScreen prompt until the app is code-signed.
- If Claude Code was installed another way (npm or WinGet), AgenEditor finds it as long as `claude.exe` is on the
  PATH or in `%USERPROFILE%\.local\bin`.
