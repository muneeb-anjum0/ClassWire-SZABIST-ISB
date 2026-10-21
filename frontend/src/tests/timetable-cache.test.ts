import { expect, test } from 'vitest';
import { isSearchCacheFresh, isUsableWeeklySource, verifiedRefreshDates } from '../services/timetableCache';
import { TimetableData } from '../types/api';

const resultAt = (savedAt: string, snapshotVersion?: number): TimetableData => ({
  for_day: 'Monday',
  for_date: '2026-10-12',
  query: '',
  message_id: null,
  items: [],
  summary: { total_items: 0, semester_breakdown: {}, unique_courses: 0, unique_faculty: 0 },
  source_cache: {
    tier: 'firestore', snapshot_version: snapshotVersion, requested_days: ['Monday'],
    refreshed_at_by_day: { Monday: savedAt },
    source_received_at_by_day: { Monday: savedAt },
  },
  search: {
    parser_version: 17, query: 'BSSE7A on Monday', saved_at: savedAt,
    answer: 'Found classes.', intent: 'schedule', days: ['Monday'], entities: {}, free_slots: {},
  },
});

test('a weekly search result remains usable until explicit refresh, regardless of age', () => {
  expect(isSearchCacheFresh(resultAt('2020-01-01T00:00:00Z', 1))).toBe(true);
  expect(isSearchCacheFresh(resultAt('2020-01-01T00:00:00Z'))).toBe(false);
});

test('the browser uses only full-week snapshots, not legacy day-scoped sources', () => {
  const source = {
    items: [], snapshot_version: 1, refreshed_at_by_day: {},
  };
  expect(isUsableWeeklySource(source)).toBe(true);
  expect(isUsableWeeklySource({ ...source, snapshot_version: 0 })).toBe(false);
});

test('the displayed update date must match the actual email date', () => {
  const result = resultAt('2026-10-22T10:00:00Z', 1);
  result.source_cache!.source_received_at_by_day = { Monday: '2026-10-12T05:00:00Z' };
  expect(verifiedRefreshDates(result)).toEqual({});
});
