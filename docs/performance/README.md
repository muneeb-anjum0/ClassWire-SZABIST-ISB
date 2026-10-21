# Performance and Optimization

ClassWire is optimized for the limits that dominate its workload: Render cold starts, Gmail round trips, repeated Firestore operations, large timetable payloads, browser startup, and broad-result rendering.

## Measured baselines

| Measurement | Current baseline |
| --- | ---: |
| Backend application import | Approximately **0.19 seconds** |
| Initial frontend JavaScript | **203.2 kB**, **64.8 kB gzip** |
| Previous initial JavaScript | **300.9 kB**, **96.7 kB gzip** |
| Initial schedule render window | **60 rows** |
| Semantic search planning | **1.17 ms mean**, **2.73 ms p95** across 2,000 warm local fixture queries |
| Backend test coverage | **64.2% branch-aware** |
| Frontend line coverage | **73.8%** |

These figures were measured locally. The semantic-search measurement used four representative query families against a 40-row synthetic acceptance fixture after warm-up. They are useful regression baselines, not claims about public-network speed, Google API latency, Firestore latency, or hosting-platform wake time.

## Backend startup

Heavy integrations are lazy. Importing the Flask application does not immediately construct Firebase, Firestore, Gmail, or parser services. The first endpoint that needs a service initializes it behind a thread-safe boundary.

The health endpoint is deliberately Flask-only. It does not contact Gmail or Firestore, so Render can detect an open and ready web process as soon as the application is serving requests.

Gunicorn binds to Render's assigned `PORT` through both the process command and the automatically discovered configuration. Startup and crash logs are streamed, lightweight requests can use threads, and worker temporary files use shared memory.

## Browser startup

- The production API origin is known immediately, avoiding a blocking discovery health check.
- No startup health probe or background bootstrap wakes Render.
- A locally remembered signed-in identity opens the cached dashboard immediately; an unreachable sleeping backend is never interpreted as a logout.
- OAuth completion remains authoritative and establishes both the signed cookie and the durable local identity marker.
- Production API traffic is reverse-proxied through the Vercel origin so signed cookies remain first-party in private browsing.
- Bootstrap returns identity and the latest timetable without reading obsolete filtering configuration.
- Login, legal pages, and the authenticated dashboard load as separate route chunks.
- Native Fetch replaced a general-purpose HTTP dependency while retaining cookies, timeout behavior, and typed errors.

Render free-tier suspension can still add provider-controlled delay. Application work has been minimized, but a sleeping machine cannot be made equivalent to an always-on paid instance.

## Search path

```text
React state
  -> exact-query IndexedDB cache
  -> normalized full-week source in IndexedDB + browser-side matching
  -> repeated-query process cache
  -> normalized source memory cache
  -> compressed Firestore source
  -> Gmail only on first snapshot creation or explicit Refresh
```

The first server result stores both the answer and its normalized weekly source in account-scoped IndexedDB. Later wording is interpreted against that source in the browser, so it does not contact or wake Render. The saved source has no automatic age boundary. Render is contacted when the full-week snapshot is absent, the user presses Refresh, or an account operation genuinely requires the server. For interactive search, Gmail is contacted only to create or explicitly refresh the full-week snapshot; scheduled daily delivery is separate.

A new device has no local source yet, so its first timetable question contacts Render once to obtain the account's persisted source without scraping Gmail. Thereafter it uses that snapshot until Refresh.

### Optional semantic inference

The query-understanding runtime is designed around the free backend's memory and CPU limits:

- eight compact TinyBERT layers and one shared encoder for intent and entity prediction;
- dynamic INT8 ONNX weights instead of a PyTorch production runtime;
- lazy artifact loading, so startup and deterministic searches do not pay model initialization cost;
- one ONNX intra-operation thread and one inter-operation thread;
- sequential execution with CPU memory arenas and retained memory patterns disabled;
- one process-wide inference lock, preventing concurrent requests from multiplying activation memory;
- one Gunicorn worker with two request threads by default;
- a maximum 96-token query window;
- an artifact gate of 30 MB and a warm CPU p95 gate of 50 ms.

The deployed v4 Kaggle artifact measured 14.77 MB, 17.13 ms warm CPU p95,
100% intent accuracy, 99.24% exact-span entity F1, and 97.46% joint exact
match on 2,640 held-out generated queries. These numbers describe that fixed
benchmark, not arbitrary real-world language. The benchmark process reached a
425.60 MiB peak before the low-memory production session settings were added.
The packaged production runtime then measured a 92.77 MiB cold-process peak and
a 225.04 ms first prediction in GitHub Actions. CI rejects future artifacts that
exceed a 192 MiB cold-process peak.

Render's protected metrics endpoint reports current RSS, peak RSS, cgroup use,
limit, and remaining headroom. Cache capacities are deliberately smaller for
large timetable sources than for compact settings and hashes. Expired entries
are purged on writes and inspection instead of retaining stale object graphs.

## Gmail efficiency

- Each weekday is queried independently only on initial creation or manual Refresh.
- Weekday message IDs allow unchanged parsed rows to be reused.
- Only changed weekday bodies are downloaded.
- Temporary Gmail request errors use bounded retry; unresolved errors do not overwrite the prior snapshot.
- An older latest weekday remains valid when other weekdays have newer messages.
- Concurrent refresh requests for one user collapse behind one lock.

## Firestore efficiency

| Optimization | Effect |
| --- | --- |
| Direct per-user document IDs | No composite index requirement on interactive paths |
| Bounded TTL caches | Warm token, settings, timetable, source, identity, and health reads avoid Firestore |
| Stable content hashes | Unchanged timetable and source documents are not rewritten |
| Gzip-compressed JSON | Repeated keys and labels occupy less document and transfer space |
| Compressed-size guard | Oversized data fails before an unsafe Firestore write |
| Source/result separation | Durable source stays server-side while ordinary search answers avoid database writes |
| Stale-source cache ordering | Fallback reuses the already decoded document rather than reading it twice |

## Browser persistence

Large authenticated results and normalized timetable sources are stored in IndexedDB rather than synchronous `localStorage`. The connection is reused, browser-side searches follow server-provided per-weekday expiry times, existing local entries migrate forward, and an unavailable IndexedDB implementation falls back without breaking the application. Recent search labels are stored separately in Firestore; local changes synchronize the next time another operation already needs Render, so history alone never wakes it.

React state keeps the current answer visible while a tab remains open. Failed background refreshes do not replace it with an empty state. Request ordering prevents a late older response from overwriting a newer search.

## Rendering

- Broad results render an initial 60-row window and expand in 60-row increments.
- Desktop and mobile layouts share the same normalized data and deterministic sorting.
- Day rows use content visibility hints for off-screen work.
- Result restoration avoids reparsing or refetching merely to repaint the last answer.
- Semester color assignment uses generated hues rather than a fixed palette.
- Static legal and login code does not inflate the authenticated dashboard entry chunk.

## Payload handling

Large JSON responses are gzip-compressed only when the browser supports compression and the result becomes smaller. Small payloads avoid compression overhead. Authenticated API responses use no-store HTTP semantics because restoration belongs to the controlled per-user browser cache, not a shared cache.

## Optimization ledger

| Constraint | Implemented response |
| --- | --- |
| Eager backend dependency initialization | Lazy request-time service construction |
| Health checks waking cloud dependencies | Constant-time application health route |
| Dashboard startup waking Render | Durable local identity and source restore with no background request |
| Production API discovery round trip | Immediate known-origin selection without probing it |
| Incognito blocking cross-site session cookies | Same-origin API proxy plus single-use OAuth handoff |
| Synchronous large-result storage | IndexedDB with connection reuse |
| Every refresh reparsing every day | Per-weekday message IDs and incremental fetch |
| Gmail throttling and transient failure | Timeouts, bounded backoff, jitter, and partial retry |
| Search reaching Gmail repeatedly | Durable normalized source separated from displayed result |
| Searches after Render sleeps | Full-week IndexedDB source and browser-side matching |
| Exact repeated browser search | IndexedDB answer reuse without a backend request |
| Device-local recent history | Compact account-scoped Firestore search history |
| Identical search recomputation | Bounded parser-versioned result cache |
| Search-time Firestore writes | Browser result persistence and server-side source persistence |
| Repeated document reads | Service-specific memory TTL caches |
| Free-tier memory pressure | One worker, serialized inference, bounded caches, and live RSS telemetry |
| Identical document writes | Stable hashes excluding volatile timestamps |
| Large Firestore payloads | Compact JSON plus gzip bytes |
| Hundreds of immediate DOM rows | Progressive render windows |
| One large frontend bundle | Route-level lazy loading and dependency removal |
| Race-prone asynchronous updates | Sequence guards, in-flight guards, and refresh locks |

## Remaining boundary

The current render window materially limits work for normal schedules. True viewport virtualization could reduce DOM work further for thousands of visible rows, but it would add complexity that the current SZABIST timetable volume does not justify.

[Back to documentation index](../README.md)
