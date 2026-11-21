# Devpost submission — Transtudy

## Name
Transtudy

## Pitch (1–2 sentences)
Transtudy turns every translation into study: speak or type, get the translation instantly, and two Google ADK agents file it into a wordbook, write example sentences and build today's review. Every agent tool call is checked by a policy-as-code guard and written to an audit log.

## The problem the agents solve
Translation apps help in the moment, but their history just scrolls away; vocabulary books are too slow to use in a shop or a conversation; chatbots need a prompt every time and never organise anything. As an exchange student in Spain I look words up every day and forget them. Transtudy connects the lookup (seconds) to the learning (five minutes later): English → Spanish lookups are filed as "Couldn't say" (speaking), Spanish → English as "Didn't catch" (listening), repeated lookups become weak words, and weak words go into today's review and lessons.

## Architecture and agent flow
- Browser (vanilla JS) → FastAPI. Voice is streamed over a WebSocket to the **Gemini Live API** for live transcription; the **Cloud Translation API** translates immediately (no LLM wait). If Live fails, a 16 kHz WAV goes to **Gemini** for transcription.
- **ADK root agent** `transtudy_coordinator` (a BaseAgent without an LLM) routes each job to one of two **Gemini** agents:
  - **Capture Agent** — tools `find_similar_entries`, `save_entry`, `bump_lookup_count`, `request_tutor_review`. It decides whether a lookup is new, the same, or a similar variant ("Hola, ¿cómo estás?" = "¿Cómo estás?"), saves or counts it, and on the 3rd lookup hands the new weak word to the Tutor.
  - **Tutor Agent** — tools `update_today_review`, `set_today_review`, `record_result`, `get_example_candidates`, `save_example`. In the background it writes an example sentence when useful and decides today's review list (max 7 words: which word to drop for the new one, and why). In lessons it decides whether a card comes back and whether a word stays on today's list.
- Gemini calls are kept minimal: 2 per text lookup, 1 per lesson answer, 1 per background review; write tools end the turn with `skip_summarization`.

## Safety
- **Prompt injection**: user text is wrapped in `<user_utterance>` (7 data tags in total) and treated as data; inputs containing tag names are rejected; translation is done by a non-LLM API.
- **Policy-as-code**: `policy.yaml` lists each agent's tools and limits; ADK `before_tool_callback` checks every call and blocks violations before they run (e.g. saved text must equal the input, counts only for search results, examples only for server-chosen words with no links or tags, max 7 words in today's review, one handoff per lookup).
- **Least privilege**: no agent can delete, change settings or run code; deleting is a human-only action with a confirm dialog.
- **Audit log**: every tool call (allowed or blocked, with reason) is in `logs/audit.jsonl` and in the in-app Agent log.
- **Proof**: a red-team script sends 16 hijacked-agent tool calls to the real guard — 16/16 blocked; a stage script sends a fooled Gemini's requests to the running app — 2 blocked, the normal one allowed.
- GDPR: audio is never stored, no location; silent recordings are never sent to Gemini.

## Built with (tags)
Gemini API, Gemini Live API, Google ADK, Google Cloud Translation API, FastAPI, Python, SQLite, JavaScript

## Repository
https://github.com/Rinkufujiwara/transtudy

## Live demo
Runs locally on the developer's laptop (`uvicorn app.main:app`), demonstrated live on stage.
