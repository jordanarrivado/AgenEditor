# Phase Two: local speech evidence and agent cut plans

Phase Two adds local transcription and source-evidence validation to the Phase One MCP server. It does
not invoke an AI planner inside the engine. An external MCP host reads the transcript and proposes cuts.

## Install and prepare a speech model

```powershell
uv sync --locked --extra transcribe
uv run ageneditor cache-model tiny
uv run ageneditor mcp --workspace .local-projects
```

`cache-model` explicitly downloads the selected faster-whisper model once. Supported presets are `tiny`,
`base` and `small`. `transcribe_asset` uses `local_files_only=True`; it never downloads a model. The
default CPU/int8 path works without CUDA. The engine decodes audio itself with the bundled `ffmpeg.exe`
(16 kHz mono float32, channels averaged as PyAV did) and passes samples to faster-whisper, so the packaged
engine excludes PyAV and its GPL x264/x265 DLLs. PyAV is still installed for development, where a parity
test compares both decoders; it stays pinned below 19 because the published faster-whisper 1.2.1 audio
decoder uses an argument removed in PyAV 19
([upstream issue](https://github.com/SYSTRAN/faster-whisper/issues/1589)).

## MCP sequence

1. Register a primary video containing speech, then create an empty timeline with the desired profile
   and target duration, as in [Phase One](MCP_PHASE1.md).
2. Call `transcribe_asset(project_id, asset_id, model_preset)`; poll `get_job_status`. A successful job's
   `output_path` identifies the immutable transcript JSON. Its filename stem is the `transcript_id`.
   `cancel_job` also accepts transcription jobs. Completed transcripts are cached by source hash,
   model preset and installed faster-whisper version.
3. Call `get_transcript(project_id, transcript_id, start_word, limit, context_words)` in pages. It
   returns source word IDs, integer microsecond timestamps, line/segment IDs, language metadata,
   observed word-gap candidates and deterministic hook hints. Word-gap candidates are review hints:
   the engine does not delete pauses automatically. Hook scores use only timing, punctuation and
   word confidence; they do not measure editorial quality.
4. The external agent submits `submit_edit_plan(project_id, plan, expected_revision)`. Each of 2–5
   ordered segments names its registered `asset_id`, `transcript_id`, first and last word ID,
   half-open source frame range, rationale and agent score. The engine checks cited words, source
   hashes, range coverage, duration and revision. It flags likely mid-thought cuts and repeated speech.
5. A valid response includes `candidate_timeline`. Review it, then call `commit_timeline` explicitly;
   submission itself does not commit or render. Invalid plans receive issues. After three attempts
   against a revision, the response requests manual review; the engine does not guess new cuts.
   A transcript with too little cited speech returns `source_insufficient`.

Transcript times are integer microseconds normalized onto the registered video's source clock.
Source frame indices remain on that asset's CFR or VFR frame grid; see [TIMEBASE.md](TIMEBASE.md).

The synthetic fixture has music/tone rather than spoken material. It is useful for checking worker
execution and failure handling, but it cannot establish word accuracy or narrative coherence.
Phase Two's human review exit gate requires consented 5–20-minute talking-head recordings.
