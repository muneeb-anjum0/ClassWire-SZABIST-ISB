# Operations and Reliability

ClassWire runs a static React client on Vercel and a Flask API on Render. Firestore provides durable per-user storage, Gmail remains the authoritative timetable source, and GitHub Actions handles scheduled delivery and continuous verification.

## Deployment topology

| Service | Responsibility |
| --- | --- |
| Vercel | Static frontend, public metadata, route delivery, and same-origin `/api` reverse proxy |
| Render | Flask and Gunicorn API, OAuth callbacks, search, ingestion, and metrics |
| Cloud Firestore | Encrypted token records, settings, timetable documents, and normalized source documents |
| Gmail API | Read-only source discovery and message retrieval |
| GitHub Actions | Pull-request quality gates and scheduled daily delivery |

## Render process behavior

- Gunicorn binds `0.0.0.0` to the platform-provided `PORT`.
- The health route avoids Firestore and Gmail network calls.
- Heavy service initialization occurs only when a request needs the service.
- Startup and crash logs are sent to standard output.
- Threaded handling supports lightweight concurrent requests without multiplying cloud clients unnecessarily.
- Shared-memory worker temporary files avoid slower disk-backed heartbeat behavior.

The free Render instance can suspend after inactivity. ClassWire leaves it asleep while local identity and the saved weekly snapshot answer searches. The first query without a snapshot, a manual Refresh, OAuth, or a server-only account operation can still incur provider-controlled cold-start delay.

## Background delivery

Daily timetable email delivery does not rely on an untracked daemon thread inside the web process. A scheduled GitHub Actions workflow calls an authenticated synchronous automation endpoint and waits for a definitive success or failure response.

Workflow concurrency prevents overlapping runs. Temporary network failures receive bounded retries. This provides observable background execution without a paid cloud worker.

## Failure recovery

| Failure | Behavior |
| --- | --- |
| Gmail temporarily unavailable during Refresh | Keep the last persisted source and show an error without overwriting it |
| Render sleeps after a successful scrape | Answer from the browser's saved weekly source without waking Render |
| One Gmail weekday request fails | Retry temporary errors; reject the incomplete refresh |
| Older saved source | Keep it until the student explicitly refreshes |
| Browser request fails | Preserve the last successful visible result |
| Private browser blocks third-party state | Keep the signed session first-party through the Vercel API proxy |
| Older request completes late | Ignore it through request sequencing |
| Concurrent refresh arrives | Join the existing per-user refresh work |
| IndexedDB unavailable | Fall back without preventing search or display |
| Oversized compressed document | Reject before attempting an unsafe Firestore write |

## Request observability

Every API response carries:

- an `X-Request-ID` for correlation;
- a `Server-Timing` duration visible in browser developer tools.

Non-health API requests emit structured JSON logs containing route template, method, status, duration, request ID, and response size. User queries, timetable rows, Gmail bodies, OAuth material, and faculty or course contents are excluded.

## Runtime metrics

The dependency-free in-process registry tracks:

- response count by route and status;
- average and p95 route latency;
- parser duration and accepted or rejected row counts;
- search interpretation and matching duration;
- matched-row volume;
- Gmail messages fetched;
- source tier: memory, Firestore, or Gmail;
- Firestore reads, writes, and deleted account documents;
- token, settings, timetable, source, and repeated-query cache hits.

A secret-protected endpoint exposes snapshots. Metrics reset when the Render process restarts, so they are diagnostic runtime counters rather than durable historical monitoring.

## Cost visibility

ClassWire does not claim access to a provider invoice. It counts the operations controlled by the application:

- normal searches use normalized rows and perform no Gmail read;
- interactive search contacts Gmail only when the full-week snapshot is first created or the user explicitly refreshes it; scheduled daily delivery is separate;
- repeated reads are absorbed by TTL caches;
- ordinary searches create no Firestore write;
- unchanged timetable and source writes are suppressed by hashes;
- only changed weekday messages are downloaded;
- browser restoration uses IndexedDB;
- no bootstrap request is made solely for startup; OAuth completion verifies the session and server-required operations verify it naturally.

These measurements support cost estimation without a paid monitoring platform or fabricated billing figures.

## Security operations

- Repository checks reject tracked environment files, private keys, service-account files, common secret formats, unexpected root entries, and executable tracked files.
- Dependency audits run for Python and npm production dependencies.
- Backend tests run with branch-aware coverage.
- Frontend behavior tests, coverage thresholds, TypeScript validation, and the production build run in CI.
- Production configuration refuses weak or missing secrets.
- Account deletion removes every owned application document and clears caches.

## Current boundaries

- Free-tier cold starts remain controlled by Render.
- In-process telemetry is not durable across restarts.
- The Gmail parser is deterministic and versioned, but a future timetable format can still require a parser update.
- Progressive rendering is appropriate for current timetable sizes; thousands of visible rows could justify viewport virtualization.
- Firestore TTL deletion of the separate last-presented-timetable document is asynchronous; the weekly source has no expiry timestamp.

[Back to documentation index](../README.md)
