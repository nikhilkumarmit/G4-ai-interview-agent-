# Mockmate · AI Mock Interview Coach

A placement practice app built on the project's React/Vite frontend, Flask API, SQLite, and LiveKit rooms. Interview text is grounded in the uploaded resume and job description. Speech, when enabled, uses Groq Whisper STT, a Groq-hosted LLM, and Groq Orpheus TTS. No OpenAI service is called. SQLite stores resumes, job descriptions, transcripts, and report data locally; keep the database file private.

## Requirements

- Python 3.10+ and Node.js 18+
- Existing Groq and LiveKit credentials in the workspace `.env` file (the backend loads it from the workspace root). Secrets are used only by the backend.
- A LiveKit server URL, API key, and API secret. The browser receives a short-lived room token only after the interview consent step.

## Backend files

Here is what each file in `backend/` does:

- `agent.py` — Runs the LiveKit voice interviewer. It connects speech recognition, the language model, and speech playback through Groq.
- `api.py` — Runs the Flask web API. It accepts documents, starts and ends interviews, saves answers, and provides secure LiveKit room tokens.
- `interview.py` — Contains the interview logic: reads documents, creates questions, tracks interview progress, saves answers, and builds the feedback report.
- `db_driver.py` — Saves and loads interview sessions from the local SQLite database.
- `prompts.py` — Holds the interviewer’s instructions and the message shown before asking for audio consent.
- `requirements.txt` — Lists the Python packages needed by the backend.
- `sample.env` — Shows the environment variable names and example settings the backend can use. It contains no real credentials.
- `run_tests.py` — Runs the scripted backend checks and prints a pass/fail table.
- `tests/test_interview.py` — Checks question grounding, follow-ups, answer progress, document safety, and score consistency.
- `interviews.sqlite3` — The local database file where interview sessions and transcripts are stored. Keep it private; it may contain personal information.

`backend/sample.env` documents supported names and defaults. Do not copy secret values into frontend files. The configured models are current Groq model IDs (`qwen/qwen3.8-27b`, `whisper-large-v3-turbo`, and `canopylabs/orpheus-v1-english`). Groq lists free-plan rate limits for these models, but its developer plan has usage-based prices, including for TTS. Check the provider dashboard and stay on the Free plan; this app does not select a paid tier or automatically retry rate-limited requests. Model availability and quotas can change. Groq describes Orpheus as a preview model; override `GROQ_TTS_MODEL` only with a currently supported Groq speech model.

The uploaded documents are untrusted content. The interviewer treats them as evidence, removes common embedded instruction patterns from question evidence, and is instructed to ignore any document instructions. Uploads are limited to 8 MB per file and 80,000 extracted characters per document. Text-only PDFs may provide no extractable content. The application does not retain microphone audio; LiveKit sends audio for the active session and Groq processes speech for transcription. Interview state and transcripts are stored in `backend/interviews.sqlite3` by default.

## Install and run

From this repository directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r backend/requirements.txt
npm --prefix frontend ci
```

Start the backend and LiveKit worker in separate terminals, with the virtual environment activated in each:

```bash
python3 backend/api.py
```

```bash
python3 backend/agent.py dev
```

Start the frontend in another terminal:

```bash
npm --prefix frontend run dev
```

Open the Vite URL (normally `http://localhost:5173`). If LiveKit does not connect, confirm `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` in the workspace `.env`, then verify worker and room connectivity. `/health` reports whether server credentials and a Groq key are present without exposing values.

## Workflow and limits

Upload or paste a resume and job description, review the detected summary, then consent before voice processing. The interview saves state after each turn. A resumed active session asks for consent again and preserves its current question. Default limits are 8 questions and 30 minutes; set `MAX_QUESTIONS` and `MAX_INTERVIEW_MINUTES` in `.env` to change them. The VAD silence threshold is 0.9 seconds to allow short thinking pauses. LiveKit handles barge-in/interruption in the room session.

Question generation is intentionally conservative and deterministic: it uses explicit project/skill evidence from the resume and includes a JD-to-resume question. With sparse documents, it says evidence is limited rather than inventing details. Behavioral prompts use STAR. Answers receive a transparent heuristic 1–5 practice rubric (relevance, structure, specificity, communication); score evidence is shown as a transcript quote. This MVP rubric is not a validated assessment instrument. Feedback is not a hiring recommendation. Speech recognition can omit words, distort accents, or mishear names and technical terms; inspect and correct transcripts before using scores. An STT error can unfairly lower a score by making a complete answer appear vague or irrelevant.

Latency is collected from LiveKit's per-turn end-to-end metric when available. WPM, long pauses, and filler counts are explicitly unavailable when timestamps, voice duration, or a complete voice transcript are missing. The app does not silently estimate them. WER is not reported without a verified reference transcript.

## Tests and audio evaluation

Run the scripted backend checks:

```bash
python3 backend/run_tests.py
```

The six required cases cover resume-grounded questions, JD/resume linkage, vague-answer follow-up, complete-answer advancement, prompt-injection handling, and refusal of prohibited personal questions. A repeatability check scores one answer five times and verifies a name-only change leaves the score unchanged. These are local workflow tests; they make no Groq calls.

No consented accented-speech sample with a verified reference transcript is included in the repository. Therefore STT, WER, accent fairness, real LiveKit latency, interruption behavior, and the deployed free-tier quotas still need a manual run with a consented sample. The app does not fabricate an audio result or WER.
