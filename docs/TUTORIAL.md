# Tutorial: a 40-second reel from a talking-head recording

This walks through the whole workflow with an AI agent (Codex or Claude Code) driving the engine over
MCP. For the native app, follow [the desktop tutorial](TUTORIAL_DESKTOP.md).

You need: AgenEditor installed ([INSTALL.md](INSTALL.md)), a speech model cached
(`ageneditor cache-model base`), an MCP host configured ([MCP_PHASE1.md](MCP_PHASE1.md)), and a
recording of yourself talking for 5–20 minutes. Optional: a few B-roll clips and a music track you
have the rights to use.

You can type the steps below to your agent in plain language; the tool names are listed so you can
check what it did.

## 1. Create a project and import media

> "Create an AgenEditor project called *Coffee reel*."

The agent calls `create_project` and tells you the project's **imports folder**. Copy your recording
(and any B-roll or music) into that folder yourself, then:

> "Register `interview.mp4` as the primary source, `pour.mp4` as B-roll and `bed.m4a` as music."

(`register_asset`). Each file is copied into `originals/`, hashed and made read-only. Your own copy is
never touched.

## 2. Transcribe

> "Transcribe the interview with the base model."

(`transcribe_asset`, then `get_job_status` until it finishes.) Everything runs locally.

## 3. Let the agent pick the cut

> "Read the transcript and propose a coherent 40-second reel with a strong opening line."

The agent pages through `get_transcript`, then calls `submit_edit_plan` with 2–5 passages, citing exact
word IDs and a rationale for each. The engine checks every citation against the transcript and the
source, warns about cuts that start or end mid-thought, and returns a candidate timeline. Nothing is
committed yet. Ask the agent to show you the passages and rationale, then:

> "Commit that plan."

(`commit_timeline` creates revision 1.)

## 4. Captions, B-roll and music

> "Add captions. Label `pour.mp4` as 'coffee pour', suggest B-roll, and place the best suggestion.
> Add the music under the speech."

(`apply_revision` with `build_captions`; `set_asset_labels`; `suggest_broll`; `apply_revision` with
`add_overlay` for the B-roll and the music.) Every change is a new revision; nothing is overwritten.

## 5. Preview and check

> "Render a preview and run QA."

(`start_render` with `final=false`, `get_job_status`, `run_qa`.) Open the preview MP4 in the project's
`renders/` folder. Read the QA report:

* **Technical** checks (decode, resolution, duration, loudness, true peak, caption bounds and
  provenance, unchanged originals) must pass.
* **Editorial** flags (jump cuts, framing, B-roll balance, uncertain caption words, hook timing) are
  hints for you to judge, not failures.

## 6. Revise

> "The word 'Kona' is captioned as 'corner' — fix it. And punch in a little on the second clip."

(`apply_revision` with `correct_word` and `set_transform`.) No re-transcription happens. Don't like
it? "Undo that" → `revert_revision` creates a new revision equal to the earlier one.

## 7. Final export

> "Render the final version and run QA."

(`start_render` with `final=true`, then `run_qa`.) The job folder `renders/<job>/` contains
`final.mp4`, `captions.srt`, `timeline.json`, `qa.json` and `render.log`.

## If something goes wrong

* `INSUFFICIENT_DISK`: free space; the engine checks before importing or rendering.
* A crash or power loss: restart. Interrupted jobs are marked `interrupted`; `ageneditor cleanup
  <project> --apply` removes their temporary files.
* `ageneditor doctor <project> --hash` checks the project and that every original is unchanged.

> **Exit-gate note.** Phase 5 requires that a new user can complete this tutorial unaided. That has not
> been tested with real users yet; see [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).
