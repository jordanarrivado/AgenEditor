# Benchmark and evaluation set

`ageneditor benchmark <manifest.json>` runs a dataset of **consented** recordings through the real
pipeline and reports only measured values. The dataset stays outside the repository.

```powershell
uv run ageneditor benchmark D:\agen-eval\manifest.json --out benchmark-results --transcribe base
```

Per recording it measures:

| Measure | How |
|---|---|
| Import time | Copy + hash + probe, wall clock |
| Transcription speed | Source seconds ÷ wall seconds (only with `--transcribe`), and the device used |
| Word error rate | Against `reference_transcript` (word-level Levenshtein, case/punctuation-insensitive) |
| Reference range validity | Human `accepted_ranges` that fall outside the source |
| Preview render speed | Output seconds ÷ wall seconds for the human reference edit at 720×1280 |
| QA vs annotations | QA checks that warned/failed but were not annotated (**candidate** false positives for a human to confirm), and annotated defects QA missed |

The report (`benchmark.json`, `benchmark.md`) includes the `ageneditor system` hardware profile, so
runs on low/mid/high machines can be compared. Do not publish speed claims from a single machine.

Not measured automatically: editorial quality, and editor adjustments (how much a person changed the
agent's cut). Record those separately during the human review.

## Manifest

Paths are relative to the manifest file. Times are seconds (decimal strings keep them exact).

```json
{
  "name": "consented talking heads v1",
  "records": [
    {
      "id": "rec01",
      "media": "rec01/interview.mp4",
      "consent": true,
      "reference_transcript": "rec01/transcript.txt",
      "accepted_ranges": [
        {"start_s": "12.40", "end_s": "24.10"},
        {"start_s": "301.0", "end_s": "329.5"}
      ],
      "known_defects": [
        {"check_id": "jump_cuts", "note": "same-angle join at 11.7 s"},
        {"check_id": "uncertain_caption_words", "note": "product name"}
      ]
    }
  ]
}
```

Records with `"consent": false` are skipped and reported as such. `check_id` values are the QA check
IDs in `qa.json` (for example `jump_cuts`, `framing`, `broll_balance`, `uncertain_caption_words`,
`hook_timing`, `silent_gaps`, `black_gaps`, `loudness`).

## Status

The harness is tested on synthetic fixtures. The 10–20 consented real recordings it is meant for do
not exist yet, so no benchmark results have been published.
