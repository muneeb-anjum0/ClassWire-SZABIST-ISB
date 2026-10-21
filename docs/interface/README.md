# Interface and Experience

ClassWire presents a conversational timetable search rather than a filter-heavy dashboard. The interface is intentionally sparse before a query and becomes information-dense only when a result needs to be inspected.

## Search composer

- A centered empty state transitions into a compact result workspace.
- Recent searches are stored per account in Firestore and mirrored locally, so the same signed-in account sees them across devices.
- Suggested questions are randomized from the current timetable context.
- Suggestion history prevents immediate repetition.
- Individual recent searches and the complete history can be cleared.
- The search-bar clear control dismisses the result and returns the composer to its centered state.
- A hidden result can be restored without rerunning the request.
- Refresh beside Search explicitly checks all six weekday emails and replaces the saved weekly snapshot.
- Rapid submissions are guarded so an older result cannot replace the latest question.

## Authentication experience

Google authentication uses a full-page redirect rather than a popup. After Google returns to the backend callback, the browser receives a short-lived handoff and exchanges it through ClassWire's own origin. This works when private browsing blocks third-party cookies and avoids opener-policy warnings from popup polling.

Logged-out startup is entirely local, so opening the public login page neither wakes Render nor generates expected-but-noisy `401` console errors.

The login page uses a restrained diagonal access band to identify read-only Gmail access and SZABIST Islamabad context without turning the page into a decorative card. A delayed Render wake message uses an amber striped treatment distinct from active search loading.

## Feedback model

Search status is rendered in the schedule flow rather than as a detached corner overlay. Success, warning, authentication, and error states use distinct colors with diagonal line treatments and restrained entrance and exit motion.

Persistent account-domain warnings are separate from temporary search feedback. A user signed in outside the expected `@szabist-isb.pk` domain sees a red warning that is not displaced by ordinary search messages.

## Desktop timetable

Desktop rows are sorted chronologically inside each weekday and present:

1. time;
2. course title and code;
3. faculty member;
4. room and campus;
5. section badge.

Weekday headers use a thin diagonal background to create structure without heavy cards. Section badges have equal geometry and deterministic colors, allowing multiple sections to remain scannable.

## Mobile timetable

Mobile uses the same chronological day stream as desktop instead of grouping the day into separate section blocks. Each compact card prioritizes course identity, followed by time, faculty, and campus. Room and section badges form one connected pill treatment with fixed geometry.

The layout avoids horizontal dependence and keeps long campus or course labels from forcing unstable card widths.

## Conflict presentation

The API returns exact overlap pairs only for intentional custom schedules. Faculty schedules, broad course searches, FYP results, and normal section lookups can contain parallel rows without being personal clashes, so they remain unmarked. For custom schedules, the interface converts connected pairs into conflict groups, moves each group's classes next to one another, and applies a subtle red diagonal pattern.

- Desktop groups use connected top, middle, and bottom row boundaries.
- Mobile cards visually join into one rounded outer group.
- Non-conflicting rows keep the normal surface.
- The existing summary warning reports the total number of overlap pairs.
- Accessible row labels identify affected classes as schedule clashes.

## Search-result presentation

The search summary uses the same content width as the schedule. It remains visually quieter than the timetable, has square edges, and reserves spacing for show and hide transitions so adjacent status rows and day headers never overlap.

Single-day results give the `Class schedule` header the same structural line background used for multi-day weekday bands, while avoiding a redundant extra weekday heading.

Faculty availability uses a dedicated presentation within the result region. Each faculty member receives a diagonal identity band, an explicit day count, and compact day rows with readable free-time slots. Desktop uses a two-column day grid while narrow screens return to one chronological column.

## Account menu

The account menu separates identity, theme, sign-out, and permanent deletion. Destructive confirmation appears as a deliberate in-menu state with explicit cancel and confirm controls rather than replacing an ordinary action label in place.

Permanent deletion clears server-held account documents, attempts Google token revocation, closes the authenticated session, and removes saved browser state.

## Persistence

- The last successful result remains in React state while the tab stays open.
- IndexedDB restores it across visits without blocking initial paint.
- A failed background refresh does not erase the visible schedule.
- Stored search state is scoped to the signed-in user.
- A deleted account clears saved timetable and search history state.

## Accessibility and motion

- Icon-only controls have explicit accessible labels.
- Menus expose menu semantics and keyboard dismissal.
- Status changes use appropriate live-region behavior.
- Reduced-motion preferences disable nonessential animation.
- Hover feedback changes the surface without moving rows.
- Text and control layouts remain usable across desktop and narrow mobile widths.

## Theme and typography

The interface uses the Manrope variable font and a shared semantic token system across light and dark themes. Color communicates section identity, status, and conflict state; it is not the only carrier of critical meaning.

## Search discoverability

The unauthenticated shell includes:

- SZABIST timetable and class-schedule titles and descriptions;
- canonical production URL metadata;
- Open Graph metadata;
- structured application data;
- crawler directives and a sitemap;
- an explicit independent-project disclaimer.

These elements allow search engines to understand the product before authentication without exposing private timetable data.

[Back to documentation index](../README.md)
