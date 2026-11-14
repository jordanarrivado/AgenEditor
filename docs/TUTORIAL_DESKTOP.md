# Desktop alpha: your AI agent edits, you review and export

This is the native Tauri application. `npm run dev` opens the separate browser demonstration.
Public release is disabled; use the local installer only for testing until the release checklist passes.

The app has three steps, in order on one screen: **Step 1 · Import assets and instructions**, **Step 2 · AI edit**,
**Step 3 · Preview**.

**Who does what.** Your external AI agent (Codex or Claude Code, connected through MCP) is the editor. It makes
every cut and every revision. You bring the footage and instructions, import the files generated outside
AgenEditor, review what the agent made, ask it for changes, and approve the final render. The desktop app does not
have manual cut or overlay editing.

## Step 1 · Import assets and instructions

1. Open AgenEditor, enter a project name, leave the target at 40 seconds, and choose **Create project**.
2. Choose **Import main footage** and select your approved video. It can be a talking-head recording or footage
   with no speech at all. The engine copies, hashes and registers
   the source. Optional B-roll and music use their own import buttons. Select an asset and use **Open selected
   source** in Step 3 to look at it; playback depends on the codecs Windows WebView2 supports.
3. Write your **Instructions for your AI editor** and choose **Save brief** (see below).

## Step 2 · AI edit

4. Connect your agent (see **Work with an external agent**). The agent reads your saved instructions with
   `get_brief`, plans from the transcript and commits every revision itself.
5. **Edit made by your AI agent** shows the current revision read-only: each cut with its source frame range, the
   reason the agent gave, and any overlays it placed. Each time the agent commits a new revision, the app shows it
   within a few seconds and says so. To change something, tell the agent; every change is a new immutable revision.
6. If the agent lists visuals the footage lacks, you prepare them outside AgenEditor and import them here (see
   **Prepare externally generated assets**).

## Step 3 · Preview

7. Choose **Render fast proxy**. The job panel shows engine progress and offers cancellation. When the
   job succeeds, choose **Open rendered preview** and review the actual rendered revision. Ask the agent for
   changes until you are happy; each one is a new revision to preview.
8. When you approve, choose **Render final MP4**, wait for success, then **Run technical QA**. Inspect warnings
   and review the content yourself. A QA report applies to one render; a newer final render needs its own QA.
9. Choose **Save verified MP4** and select a new filename outside the project workspace. Existing files
   are not overwritten. The project keeps its render, timeline and QA artifacts.

## Instructions and requirements

Under **Instructions for your AI editor**, write what the edit should say and how, paste the script if you have one, describe the reference video (a note or link; AgenEditor
does not import it), and add each requirement as a checklist item, for example "Product on screen by 0:05".
Choose **Save brief**. Your agent reads it with `get_brief` but cannot change it.

After you watch a render, set each requirement to **Met** or **Waived**. This is your decision, not the agent's.
The final QA report then includes a `brief_checklist` check: it warns while any item is still open and passes
when all are confirmed. AgenEditor does not check the video against the brief.

## Prepare externally generated assets

The agent lists the visuals the footage lacks as `asset_requests`. Paste that JSON into **Prepare assets** and
choose **Load requests**. Copy each request's prompt into the named external tool. Choose **Import file for…** and
select the resulting video, PNG or JPEG file. AgenEditor copies, probes and hashes it as **AI-generated
(external)**, then saves the link between its asset ID and the request in the project database. You can also link a
previously imported generated asset from the request card. Alternatively, drag one file from File Explorer onto a
pending request card; it is imported and linked the same way, labelled with whatever you typed in **External
tool**. (File dragging has not been verified in the packaged app; the buttons are the reliable path.)
Choose the **Drop** button only when you intentionally omit a request. It does not import anything.
If a linked file has a flaw you are willing to live with, type it under **Known issue you accept** and choose
**Accept with issue**. The note is saved in the project's known-issues log and the file stays in use. Resetting or
dropping the request clears it.

You import and approve the files; the agent places them. A still image or B-roll clip appears under **Overlays**
once the agent has put it on the timeline.

Saving an edit or starting a render stays blocked while any configured request is pending, including when your
agent tries to commit. Checklist changes use version checks, so a concurrent session cannot silently overwrite
them.

## Work with an external agent

Use **Install tiny model** for an explicit model download, then **Transcribe speech** for local speech
analysis. Skip this for footage without speech. The agent can still cut it, and the render keeps a silent audio track.
QA then warns that loudness could not be measured, because there is nothing to measure. Synthetic fixture footage contains no speech and is unsuitable for evaluating accuracy.

Choose **Copy MCP setup for your agent**. Configure your external MCP host with
the copied executable and arguments, including `--shared-service`. The desktop and host must use that
same service. A separate standalone MCP server must not be pointed at the live desktop workspace. The copied
message tells the agent to read your saved instructions with `get_brief`, that it is the editor, and that it must commit
each revision itself with `commit_timeline`. Your instructions are not pasted into the message.

The host may read transcripts, source filenames, metadata and word IDs and send them to its model
provider. AgenEditor does not upload the raw media. Review the host's privacy settings before connecting.

Captions, B-roll, audio and every cut change use the MCP tools. If the agent's commit is rejected (for example
because requests are pending or a newer revision exists), it is told why and must retry.

Projects are stored under `%LOCALAPPDATA%\com.ageneditor.desktop\projects`. The engine uses a private
service discovery directory under `%LOCALAPPDATA%\AgenEditor\services`; do not share its credentials.

This tutorial has not yet passed the clean-Windows or unaided-new-user acceptance tests.
