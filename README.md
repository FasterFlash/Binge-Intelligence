# Binge Intelligence

> A domain-grounded AI chatbot for a Netflix-style streaming platform. It knows your watch history, your taste, your subscription — and it knows what it *isn't allowed* to tell you about anyone else.

<p align="center">
  <img src="docs/images/01-hero.png" width="900" alt="Binge Intelligence hero">
  <br>
  <em><strong>IMAGE 1 — HERO:</strong> The consumer chat open on the empty state. The Last Watched rail along the top shows real TMDB poster artwork, four category chips underneath, the compact chat input anchored at the bottom. This is the first thing a user sees after logging in.</em>
</p>

---

## Why this exists

I watch Netflix a lot. The product itself is professional — the UI is some of the best consumer software on the planet. But I noticed something off: every e-commerce app I open has an AI assistant now. Amazon has one, Flipkart has one, Zomato has one. They're not forced on you, they're just *there* — a feature you can try when you want.

Entertainment doesn't have this yet. And it's a strange gap, because streaming is one of the few domains where a chatbot would actually be *useful*. The platform already knows what I watched, when I watched it, how much of each episode I finished, what I abandoned halfway, when my subscription started, what genres I keep returning to, what time of day I binge. That's a dense, domain-restricted knowledge base — exactly the shape where an LLM-backed assistant can be grounded and non-hallucinatory.

So I built one. From the ground up. Simulated the users and their behavior, built the catalog, designed the agent, wrote the safety layers, shipped the UI. **Binge Intelligence** is that project.

It can answer:

- Questions about *your* data (watch history, abandonment patterns, taste profile, subscription periods, time-of-day habits)
- Questions about the catalog (what to watch tonight, something short, by this director, with this actor, critically acclaimed)
- Aggregate questions about the user base ("what do people my age in my country watch?") — without ever leaking individual data

It cannot (and refuses to) answer:

- "What is my friend watching, her user ID is 32" — denied, every time
- "Recommend an 18+ title" to a kid account — refused with a kid-friendly tone and an age-appropriate alternative
- Anything that would require ignoring its own system rules — prompt-injection attempts are detected, logged, and sandboxed

The chatbot is a *feature*, not a replacement for the normal UI. You use it when you want to.

---

## Demo

<p align="center">
  <img src="docs/images/02-picker.png" width="900" alt="Who's watching picker">
  <br>
  <em><strong>IMAGE 2 — PICKER:</strong> The Who's Watching screen with filters applied — Country = IN, age slider 18–25, taste type = Night Owl. Below, a 3-column grid of matching user cards with archetype badge, country/age chip, cohort year, titles-watched count, and a one-line taste blurb.</em>
</p>

<p align="center">
  <img src="docs/images/03-chat-recs.png" width="900" alt="Chat with recommendations">
  <br>
  <em><strong>IMAGE 3 — CHAT WITH RECS:</strong> A GenZ-voiced response to "pick me something based on my taste". User bubble top-right in red-tinted charcoal, AI bubble left with the red B avatar. Below the AI response, a Picked for You row showing 3 real poster cards with title, year, maturity badge, and genre chips.</em>
</p>

<p align="center">
  <img src="docs/images/04-title-detail.png" width="900" alt="Title deep-dive view">
  <br>
  <em><strong>IMAGE 4 — TITLE DEEP-DIVE:</strong> Click any poster → full detail view with a backdrop hero, 200px poster on the left, title/year/runtime/rating header, tagline, synopsis, and a cast carousel of 8 circular headshots. Three contextual follow-up chips at the bottom ("Something similar to…", "Why would I like…", "Who else is in…").</em>
</p>

<p align="center">
  <img src="docs/images/05-maturity-refusal.png" width="700" alt="Kid-safe refusal">
  <br>
  <em><strong>IMAGE 5 — MATURITY REFUSAL:</strong> A kid account (age 10, ceiling 7+) asking about an 18+ title. Response declines in a cheerful tone with no scolding and immediately offers two age-appropriate picks. The audit log for this turn shows the maturity-gate event firing.</em>
</p>

<p align="center">
  <img src="docs/images/06-audit-dashboard.png" width="900" alt="Dev audit dashboard">
  <br>
  <em><strong>IMAGE 6 — AUDIT DASHBOARD:</strong> The dev-side observability app on port 8502. 8 KPI tiles across two rows (Sessions, User Messages, Tool Calls with failure %, Judge Pass Rate, LLM Calls, Tokens, SQL Fallbacks, Guards Tripped), the sessions-per-hour trend chart below, then the filter row and the 3-column session card grid.</em>
</p>

<p align="center">
  <img src="docs/images/07-audit-session.png" width="900" alt="Session drill-in">
  <br>
  <em><strong>IMAGE 7 — SESSION DRILL-IN:</strong> Click any session card → two-column detail view. Left: the full transcript as chat bubbles with model/tokens/latency printed under each AI message. Right: event timeline — color-coded tiles for llm_call (blue), tool_called (green), sql_fallback (purple with the rewritten SQL shown), judge_verdict (green/yellow/red by rating).</em>
</p>

---

## What it does, at a glance

**For the user:**
- Chat with the platform. Ask what to watch. Ask why you abandoned something. Ask what people like you are watching.
- Starter prompt chips grouped into *For you / About me / Discover / By mood* — users don't have to come up with questions themselves.
- Last Watched rail of real poster artwork on the empty state.
- Click any poster → title deep-dive with cast carousel.
- Follow-up chips after each AI reply ("More like these", "Something different").
- Chat history persists across sessions. Close the browser, come back a week later, every message is still there.
- WhatsApp-style bubbles — user on the right, AI on the left, bubbles sized to content.
- Age-aware voice. A kid gets simple cheerful language. A 22-year-old gets GenZ slang. An adult gets a conversational-professional tone. Direct from a persona module keyed off the user's age.
- Streaming responses — tokens render as the LLM emits them. No "wait 10 seconds then watch a wall of text appear."

**For the developer / platform:**
- Full audit of every chat turn. Which tool was called, with what args, what came back, what the judge said about it, how long the LLM took, how many tokens burned.
- A separate dev-only observability app that reads from the same tables the chat writes to. KPI tiles, trend charts, filterable session list, drill-in with color-coded event timeline.
- Audit log rotation into object storage so Postgres stays snappy as the chatbot runs for months.

---

## Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                       Streamlit (two apps)                        │
│                                                                   │
│   Consumer chat  (port 8501)      │     Dev audit  (port 8502)    │
│   • who's watching                │     • KPI tiles + trend       │
│   • chat UI (streaming bubbles)   │     • session list            │
│   • title deep-dive               │     • transcript + events     │
│   • last watched rail             │                               │
└──────┬────────────────────────────┴──────────────┬────────────────┘
       │                                           │
       ▼                                           ▼
┌───────────────────────────────────────────────────────────────────┐
│                         Orchestrator                              │
│                    (ReAct loop, max 6 steps)                      │
│                                                                   │
│   per turn:                                                       │
│     1. rate-limit guard  (30 req/hr/user, in-memory sliding win)  │
│     2. prompt-injection sniff  (regex, logs + sandboxes)          │
│     3. build system prompt:                                       │
│          • base rules                                             │
│          • persona directive  (kid / teen / genz / adult / older) │
│          • user context  (age, country, maturity ceiling)         │
│          • policy chunks  (pgvector KNN + keyword overlay)        │
│     4. LLM.chat(messages, tools) with streaming                   │
│     5. if tool_calls:                                             │
│          • authorize (strip user_id for SELF_DATA)                │
│          • execute scoped handler                                 │
│          • LLM judge rates the output (pass / weak / fail)        │
│          • log everything                                         │
│          • continue loop                                          │
│        else: persist assistant text, return                       │
└──────┬────────────────────────────────────────┬───────────────────┘
       │                                        │
       ▼                                        ▼
┌─────────────────────┐              ┌──────────────────────────────┐
│   LLM providers     │              │        Data layer            │
│                     │              │                              │
│  3-tier fallback:   │              │  ┌────────────────────────┐  │
│  ┌───────────────┐  │              │  │  Postgres              │  │
│  │ Groq llama-   │  │              │  │  + pgvector            │  │
│  │ 3.1-8b (fast) │  │              │  │                        │  │
│  └───────┬───────┘  │              │  │  catalog (3K titles)   │  │
│          │ 429?     │              │  │  behavior (events,     │  │
│          ▼          │              │  │   sessions, progress)  │  │
│  ┌───────────────┐  │              │  │  gold aggregates       │  │
│  │ Groq llama-   │  │              │  │  people / cast / crew  │  │
│  │ 3.3-70b       │  │              │  │  agent sessions,       │  │
│  └───────┬───────┘  │              │  │   messages, events     │  │
│          │ overload │              │  │  title_embeddings      │  │
│          ▼          │              │  │  policy_chunks         │  │
│  ┌───────────────┐  │              │  └────────────────────────┘  │
│  │ Gemini flash  │  │              │                              │
│  └───────────────┘  │              │  ┌────────────────────────┐  │
│                     │              │  │  MinIO (S3-compatible) │  │
│  user never sees    │              │  │  posters/   backdrops/ │  │
│  the retry — just   │              │  │  cast/      audit/     │  │
│  a bit more latency │              │  │                        │  │
└─────────────────────┘              │  └────────────────────────┘  │
                                     │                              │
                                     │  ┌────────────────────────┐  │
                                     │  │  TMDB  (external)      │  │
                                     │  │  posters, backdrops,   │  │
                                     │  │  cast, keywords,       │  │
                                     │  │  ratings, taglines     │  │
                                     │  └────────────────────────┘  │
                                     └──────────────────────────────┘
```

<p align="center">
  <em><strong>IMAGE 8 — ARCHITECTURE (optional):</strong> A polished SVG/Figma version of the diagram above, if you want to replace the ASCII art with real design.</em>
</p>

---

## The stack

| Layer | Choice | Why |
|---|---|---|
| OLTP | **Postgres 15** with **pgvector** | Single source of truth for catalog, behavior, sessions, embeddings, policy chunks. One index set, one connection pool, one truth. |
| Semantic search | **sentence-transformers/all-MiniLM-L6-v2** (384-dim) | Free, local, fast. Embeds synopsis + tagline + keywords + cast + director per title. IVFFlat index for approximate KNN. |
| LLM providers | **Groq** (Llama 3.1 / 3.3) + **Google Gemini** (3.5-flash-lite) | Groq is fast and cheap for the default tier; Gemini is the fallback. Both reached via raw HTTP (no vendor SDKs) through a normalized interface. |
| Object storage | **MinIO** (S3-compatible) | Posters, backdrops, cast headshots, and rotated audit logs. Public-read policy on `posters/`, `backdrops/`, `cast/` prefixes so the UI loads images directly. |
| Catalog source | **TMDB API** | Ground truth for 2896 titles — IDs, posters, cast, keywords, taglines, TMDB ratings. Attribution per their TOS at the bottom of the chat view. |
| UI | **Streamlit** | Two separate apps on separate ports. One consumer (dark Netflix theme, custom HTML chat bubbles), one dev-audit (KPI tiles + trends + session drill-in). |
| Agent SDK | None | The ReAct loop is 200 lines of Python. Scoped tools register via a `@tool` decorator. No LangChain, no LlamaIndex — the whole thing is readable in one sitting. |

---

## Safety by design

This is the part I'm proudest of. Every claim below maps to real code you can read.

### Four tool scopes

Every one of the 20+ agent tools is tagged with a `ToolScope`:

- **`SELF_DATA`** — queries about the authenticated user. The `user_id` is injected by the orchestrator from the auth context; if the LLM passes `user_id` as an argument, the guard strips it. The model literally cannot query another user's data through these tools.
- **`CATALOG`** — public title info. No per-user filtering.
- **`AGGREGATE`** — population-level stats. Fine to answer "what do 20-year-olds in India watch most."
- **`OTHER_USER`** — denied by default. Any tool tagged this way gets refused at the auth gate before the handler ever runs.

### SQL fallback — the escape hatch, but a safe one

When the LLM encounters a question no scoped tool fits, it has an escape hatch: `run_sql_fallback(sql, intent)`. Before any query runs, five layers of validation:

1. **Single-statement only** — multi-statement queries (`SELECT ...; DROP ...`) are rejected.
2. **SELECT or WITH only** — the first keyword must be `select` or `with`. Any `INSERT`/`UPDATE`/`DELETE`/`DROP`/etc. is refused, including CTE-based writes (`WITH x AS (DELETE ...)`).
3. **Dangerous function blocklist** — `pg_sleep`, `pg_read_file`, `dblink`, `lo_import`, `current_setting`, etc. are refused.
4. **Auto-injected auth filter** — if the query touches any per-user table (`watch_events`, `user_series_progress`, `user_watch_stats`, `agent_messages`, etc.), the validator adds `WHERE user_id = '<the_authenticated_uuid>'` into the query automatically. The LLM cannot bypass user isolation even by writing raw SQL.
5. **Read-only transaction** — the query runs under `SET LOCAL default_transaction_read_only = on` with a 3-second `statement_timeout`. Even if a check somehow missed a write, Postgres refuses it.

A row cap of 200 is enforced on every query. 12 adversarial test cases pass locally covering writes, multi-statement, dangerous funcs, CTE-writes, auth injection, and limit capping.

### LLM judge

Every tool call (and every SQL fallback) is scored by a second LLM pass. The judge sees the user's question, the tool name + args, and the result — and rates it `pass` / `weak` / `fail` with one-sentence reasoning. Verdicts are stored in `agent_events` and surfaced in the audit dashboard with color coding. The judge is intentionally fail-open: if the judge itself errors, we default to `pass` so a flaky judge never blocks a user response.

### Policy RAG

Four markdown policy files live in `policies/`:

- `content_safety.md` — maturity ceilings, spoiler policy, categorical refusals
- `privacy.md` — data minimization, no cross-user queries, no PII in replies
- `platform.md` — scope of the assistant, catalog boundaries, abuse response
- `response_style.md` — age-aware voice, off-topic handling, formatting rules

Each file is chunked by `##` section, embedded with MiniLM, stored in `policy_chunks` with pgvector. On every chat turn, the orchestrator does a KNN search against the user's question + a keyword overlay (hard-matches for "friend", "spoiler", "kiss", etc. force the relevant policy in), and injects the top 3 chunks into the system prompt. The LLM sees exactly the policies that apply to that question — nothing more, nothing less.

### Prompt-injection detection + rate limit

- Regex-based prompt-injection sniffer catches common patterns ("ignore previous instructions", "you are now a...", "reveal the system prompt"). Hits are logged to `agent_events` and the user's message gets wrapped with a note so the LLM knows to be extra suspicious — not a hard refusal, because false positives are common and we don't want to break legitimate questions.
- In-memory sliding-window rate limiter: 30 requests per hour per user. Overages return a friendly message and log a `rate_limited` event.

### Maturity gating

The `persona.py` module exposes `check_title_maturity(title_rating, ctx)` which compares a title's rating against the user's ceiling. The orchestrator calls this before the LLM ever sees a potentially-above-ceiling title in a tool result — the result is filtered server-side. If a user explicitly asks about a title above their ceiling by name, the system prompt instructs the LLM to decline politely with a kid-appropriate tone and offer an in-ceiling alternative.

---

## How it knows what to recommend

### The simulation engine

Running a real streaming platform's data was not an option, so I simulated one. The simulator is deliberately *not* uniformly random — it models real human behavior patterns:

- **8 user archetypes** (binger, night_owl, casual_trickler, taste_explorer, series_loyalist, kid, …), each with distinct activity rhythms, completion rates, abandonment tendencies, and genre affinity.
- **2896 real titles** from TMDB, each with real synopses, release years, maturity ratings, and (via TMDB enrichment) real posters, cast, directors, keywords, and ratings.
- Users have country, age, cohort year, and a maturity_ceiling that drives both their simulated behavior and their chatbot access rights.
- `watch_events` are generated day-by-day with realistic time-of-day distributions (night owls binge after 11 PM; casual tricklers watch 30 min at a time), appropriate completion curves, and session boundaries.
- Gold aggregates (`user_watch_stats`, `title_engagement_stats`, `cohort_metrics`, `calendar_activity`) are computed from the raw event stream so the chat tools return in <100 ms.

The result: 2896 titles × thousands of users × hundreds of thousands of watch events, with internally consistent patterns that let the chatbot say meaningful things about any individual.

### TMDB enrichment

Phase B of the catalog pipeline hits TMDB's `append_to_response` API to pull everything we need for a title in a single call: poster + backdrop paths, 10 top-billed cast, directors / creators / writers / showrunners, keywords, tagline, IMDb ID, TMDB rating + vote count. Posters go into MinIO under `posters/`, backdrops under `backdrops/`, cast headshots under `cast/`. All idempotent + resumable (checks MinIO for existing objects before re-downloading, skips titles whose metadata is already in Postgres).

Fallback: titles that don't match on TMDB (rare, since the catalog was seeded from TMDB IDs in the first place) render as deterministic CSS-gradient placeholders in the UI — the gradient is seeded by the title name, so the same title always looks the same.

### Semantic search

Every title gets a 384-dim embedding of its *corpus*, which is a concatenation of:

- Title name (soft anchor)
- Tagline
- Synopsis (first 2000 chars)
- Keywords (`Themes: time-travel, found-footage, …`)
- Director credit (`Directed by Villeneuve`)
- Top 5 cast (`Starring Chalamet, Zendaya, …`)

Stored in `title_embeddings` with pgvector, indexed with IVFFlat for approximate KNN. Users saying *"something with heist energy"* or *"a Villeneuve-style sci-fi"* get matched via cast + keyword signal, not just synopsis prose.

Content hash includes the whole corpus — change any field and the row re-embeds on the next sync.

---

## Reliability

### 3-layer LLM fallback

The `FallbackProvider` wraps an ordered chain of `(provider, model)` pairs. On any exception from a tier, it moves to the next one. The default chain is:

```
groq:llama-3.1-8b-instant         →  cheap + fast, 95% of traffic
groq:llama-3.3-70b-versatile      →  if 429 or model overload
gemini:gemini-3.5-flash-lite      →  if entire Groq region is down
```

Configurable via `LLM_FALLBACK_CHAIN` env var. Streaming works through the chain — if a tier dies mid-stream, the next tier re-answers from scratch (user sees a slight pause then the real response). The user never sees an error; just a bit more latency on the (rare) days when a provider is having a bad time.

A missing API key for any tier is handled gracefully: that tier is dropped from the chain at startup with a log line, so dev setups with only one key still work.

### Full audit, zero data loss

Every chat turn writes to three tables:

- `agent_sessions` — one row per conversation
- `agent_messages` — every user / assistant / tool-result message, with model used, tokens, latency per message
- `agent_events` — the detailed audit log (LLM calls, tool calls, SQL fallbacks, judge verdicts, refusals, injection detections, rate-limits, errors)

Nothing is lost on restart. Nothing is lost on container recycle. If a user comes back three months later and asks "what did I say about horror movies last winter?" — the answer is in Postgres.

### Audit log rotation

`agent_events` grows roughly 20-30 rows per chat turn. A rotation script (`sim_09_rotate_audit.py`) runs nightly, serializes events older than 30 days to gzipped NDJSON, uploads to MinIO under `audit/<YYYY-MM-DD>.jsonl.gz`, and only then deletes the rows from Postgres. Crash-safe (delete happens only after upload succeeds); idempotent (skips days whose archive already exists).

---

## Observability

The dev audit dashboard is a separate Streamlit app on port 8502 that reads from the same tables the chat writes to. No shared state, no coupling.

**Top of page:** 8 KPI tiles across two rows, scoped to a time window (24h / 7 days / 30 days / all time):
- Sessions + unique users
- User messages
- Tool calls + failure rate
- Judge pass rate (color-coded by verdict breakdown)
- LLM calls + avg latency
- Tokens burned
- SQL fallbacks
- Guards tripped (refusals + injection detections + rate limits)

**Below:** sessions-per-hour (or per-day) trend chart.

**Session filters:** archetype multiselect, country multiselect, judge-fails-only toggle.

**Session grid:** 3-column cards showing archetype, user info, first-message preview, turn count, tool count, fail chip if any judge verdict in that session was `fail`, humanized timestamp.

**Click a card:** full drill-in. Transcript on the left (chat bubbles with model/tokens/latency printed under each AI message). Event timeline on the right with color-coded tiles — llm_call blue, tool_called green, sql_fallback purple with the rewritten SQL displayed, judge_verdict green/yellow/red by rating, refusal orange, prompt_injection red.

---

## Running it locally

### Prerequisites

- Python 3.11+
- Docker + Docker Compose (for Postgres + MinIO)
- TMDB API key (free, from themoviedb.org)
- Groq and/or Gemini API keys

### Setup

```bash
# 1. Clone + install
git clone <your-github-url> binge-intelligence
cd binge-intelligence
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt

# 2. Env vars
cp .env.example .env
# edit .env with your TMDB key + Groq/Gemini keys

# 3. Spin up Postgres + MinIO
docker-compose up -d

# 4. Create tables
python -m app.models.title
python -m app.models.user
python -m app.models.behavior
python -m app.models.gold
python -m app.models.agent
python -m app.models.people

# 5. Seed data (runs 1 after the other, each is idempotent)
python -m scripts.sim_01_users              # ~500 simulated users
python -m scripts.sim_02_lifecycle          # subscription periods
python -m scripts.sim_03_behavior           # watch events (~5 min)
python -m scripts.sim_04_gold               # gold aggregates
python -m scripts.sim_05_embed_catalog      # title embeddings
python -m scripts.sim_06_index_policies     # policy pgvector index
python -m scripts.sim_07_fetch_media        # posters + backdrops from TMDB (~5-10 min)
python -m scripts.sim_08_fetch_credits      # cast + crew + keywords (~15-20 min)

# 6. Run the two UIs on separate ports
streamlit run app/ui/chat.py --server.port 8501            # consumer chat
streamlit run app/ui/streamlit_app.py --server.port 8502   # dev audit
```

Open `http://localhost:8501` for the chat, `http://localhost:8502` for the dashboard.

---

## Project structure

```
binge-intelligence/
├── app/
│   ├── agent/
│   │   ├── auth.py              # AuthContext, user loading
│   │   ├── guards.py            # rate limit, prompt-injection, scope enforcement
│   │   ├── judge.py             # LLM judge for tool/SQL output verification
│   │   ├── orchestrator.py      # the ReAct loop
│   │   ├── persona.py           # age-aware tone + maturity gate
│   │   ├── policy_rag.py        # pgvector-backed policy retrieval
│   │   ├── providers.py         # LLM abstraction + 3-tier fallback
│   │   ├── session.py           # chat session + message persistence
│   │   ├── sql_fallback.py      # safe SQL validator + runner
│   │   ├── telemetry.py         # agent_events logging
│   │   └── tools.py             # 20+ scoped tools (self/catalog/aggregate)
│   ├── models/                  # SQLAlchemy models
│   ├── storage/
│   │   └── minio_client.py      # S3 wrapper, lazy bucket creation + public policy
│   ├── tmdb/
│   │   └── client.py            # throttled TMDB REST client
│   └── ui/
│       ├── chat.py              # consumer chat (port 8501)
│       ├── streamlit_app.py     # dev audit dashboard (port 8502)
│       ├── _theme.py            # dark Netflix CSS + animations
│       └── _prompts.py          # starter-prompt catalog
├── policies/                    # 4 markdown policy docs (embedded into pgvector)
├── scripts/
│   ├── sim_01_users.py          # simulate users
│   ├── sim_02_lifecycle.py      # subscription timelines
│   ├── sim_03_behavior.py       # watch events
│   ├── sim_04_gold.py           # gold aggregates
│   ├── sim_05_embed_catalog.py  # title embeddings (wider corpus)
│   ├── sim_06_index_policies.py # policy embeddings
│   ├── sim_07_fetch_media.py    # TMDB posters + backdrops → MinIO
│   ├── sim_08_fetch_credits.py  # TMDB cast + crew + keywords
│   ├── sim_09_rotate_audit.py   # nightly audit log → MinIO
│   └── agent_cli.py             # terminal REPL (useful for debugging)
├── docker-compose.yml           # Postgres + MinIO
├── .env.example
└── README.md
```

---

## Design decisions (and some honest limits)

**Why no agent framework?** I didn't want magic. The ReAct loop is clearer when it's 200 lines you can read than when it's a 10-dep import graph of abstractions.

**Why Streamlit?** It's one of the few frameworks where a single developer can ship both a polished consumer UI and a legitimate dev dashboard in days, not weeks. The tradeoff is you fight the framework for pixel-perfect layouts — but you win on iteration speed, and that's the right trade for a project this size.

**Why no Redis?** The rate limiter is in-process. For a single-worker Streamlit app this is correct. The moment you horizontally scale, it moves to Redis — the interface in `guards.py::RateLimiter` is already shaped for that swap.

**Why no DuckDB?** All aggregate queries today run on Postgres and they're fast enough — `user_watch_stats` and `title_engagement_stats` are precomputed gold tables. For a real production platform with hundreds of millions of `watch_events` rows, a DuckDB sidecar reading Parquet exports from MinIO is where the heavy analytical scans would live. The architecture is set up for it (gold tables → periodic Parquet dump → DuckDB reads from MinIO), but it's not needed at this scale.

**Why no tests?** For a solo portfolio project built in a short window, honestly: I validated the SQL fallback with 12 adversarial cases manually, I'd rather be honest about that than ship a weak pytest suite. For a team context, every guard, every tool handler, and the SQL validator would get real coverage.

---

## What's next

If this were going into production, the next three things I'd build:

1. **Streaming judge** — currently the judge runs synchronously after each tool call. On an ambitious interactive turn (3+ tool calls) that adds up. The judge can run in the background and surface verdicts after the fact.
2. **Cross-session memory** — "I hate horror" said in session 1 is lost by session 2. A thin `user_preferences` table + a `remember` / `forget` tool pair handles it.
3. **DuckDB OLAP sidecar** — nightly Parquet export of gold tables to MinIO, DuckDB queries them for cohort retention curves and title heatmaps. Current Postgres aggregates are fast enough for anything the chat tools do; this is purely for a future analytics layer.

Lower-priority: My List / save-for-later, trailer embeds, admin tooling for rate-limit overrides, per-user cost dashboards, mobile layout polish.

---

## Credits

- **TMDB** — catalog, posters, cast, keywords. This product uses the TMDB API but is not endorsed or certified by TMDB. See [themoviedb.org](https://www.themoviedb.org).
- **sentence-transformers / all-MiniLM-L6-v2** — the embedding model that powers the title and policy search.
- **pgvector** — the Postgres extension that makes semantic search a one-table query.
- **Streamlit** — for the UI runtime.

---

## License

MIT. Do whatever you want with the code. If you deploy a derivative that uses the TMDB API, keep the attribution in your UI per their TOS.
