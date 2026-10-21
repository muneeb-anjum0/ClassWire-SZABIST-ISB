import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { TimetableData } from '../types/api';

const apiMocks = vi.hoisted(() => ({
  getLatestTimetable: vi.fn(),
  initialize: vi.fn(),
  saveSearchHistory: vi.fn(),
  searchTimetable: vi.fn(),
}));

vi.mock('../services/api', () => ({
  BACKEND_WAKE_EVENT: 'backend-wake-state',
  apiService: apiMocks,
  getApiErrorMessage: () => undefined,
}));

import { useDashboardController } from '../features/dashboard/useDashboardController';

const makeSearchData = (query: string, answer: string, items: TimetableData['items'] = []): TimetableData => ({
  for_day: 'Monday',
  for_date: '',
  query: '',
  message_id: null,
  items,
  summary: {
    total_items: items.length,
    semester_breakdown: {},
    unique_courses: items.length,
    unique_faculty: 0,
  },
  search: {
    parser_version: 17,
    query,
    answer,
    intent: 'schedule',
    days: ['Monday'],
    entities: {},
    free_slots: {},
    recognized: items.length > 0,
    saved_at: new Date().toISOString(),
  },
  source_cache: {
    tier: 'firestore', snapshot_version: 1, requested_days: ['Monday'],
    refreshed_at_by_day: {}, source_items: items,
  },
});

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

test('a user search supersedes a slower background result restore', async () => {
  const query = 'BSSE7A timetable on Monday';
  const matchingItems = [{
    semester: 'BS(SE)-7A',
    course_title: 'Software Project Management',
    schedule_day: 'Monday',
  }];
  const currentResult = makeSearchData(query, 'Found 3 classes for BS(SE)-7A on Monday.', matchingItems);
  apiMocks.searchTimetable.mockResolvedValue({
    success: true,
    data: currentResult,
    message: currentResult.search?.answer,
    timestamp: '2026-09-20T02:33:00',
  });

  const { result } = renderHook(() => useDashboardController({
    bootstrap: null,
    deleteAccount: vi.fn(),
    isAuthenticated: true,
    loading: false,
    logout: vi.fn(),
    user: { id: 'student', email: 'student@example.com' },
  }));

  expect(apiMocks.getLatestTimetable).not.toHaveBeenCalled();

  await act(async () => {
    await result.current.runSmartSearch(query);
  });

  expect(apiMocks.searchTimetable).toHaveBeenCalledWith(query, false);
  expect(result.current.timetableData?.search?.query).toBe(query);
  expect(result.current.timetableData?.items).toHaveLength(1);

  await act(async () => {
    result.current.clearSmartSearch();
    await Promise.resolve();
  });
  expect(result.current.searchQuery).toBe('');
  expect(result.current.timetableData).toBeNull();
});

test('cached results render before session verification and survive a failed refresh', async () => {
  const cachedQuery = 'When is Zainab free on Monday?';
  const cachedResult = makeSearchData(cachedQuery, 'Faculty availability', [{
    faculty: 'Zainab Iftikhar Chaudhary',
    schedule_day: 'Monday',
    course_title: 'Algorithms',
  }]);
  localStorage.setItem(
    'classwire:v2:last-timetable:student@example.com',
    JSON.stringify(cachedResult),
  );
  apiMocks.getLatestTimetable.mockRejectedValue(new Error('Backend must remain asleep'));

  const auth = {
    bootstrap: null,
    deleteAccount: vi.fn(),
    isAuthenticated: true,
    logout: vi.fn(),
    user: { id: 'student', email: 'student@example.com' },
  };
  const { result, rerender } = renderHook(
    ({ loading }) => useDashboardController({ ...auth, loading }),
    { initialProps: { loading: true } },
  );

  await waitFor(() => expect(result.current.timetableData?.search?.query).toBe(cachedQuery));
  expect(apiMocks.getLatestTimetable).not.toHaveBeenCalled();

  rerender({ loading: false });
  expect(apiMocks.getLatestTimetable).not.toHaveBeenCalled();
  expect(result.current.timetableData?.search?.query).toBe(cachedQuery);
  expect(result.current.timetableData?.items).toHaveLength(1);
});

test('an exact repeated query renders from the device cache without a second API search', async () => {
  const query = 'Unique BSSE7A timetable cache check';
  const resultData = makeSearchData(query, 'Found one class.', [{
    semester: 'BS(SE)-7A',
    course_title: 'Software Project Management',
    schedule_day: 'Monday',
  }]);
  apiMocks.getLatestTimetable.mockResolvedValue({ success: true, data: null });
  apiMocks.searchTimetable.mockResolvedValue({
    success: true,
    data: resultData,
    message: resultData.search?.answer,
  });

  const { result } = renderHook(() => useDashboardController({
    bootstrap: null,
    deleteAccount: vi.fn(),
    isAuthenticated: true,
    loading: false,
    logout: vi.fn(),
    user: { id: 'repeat-cache-user', email: 'repeat-cache@example.com' },
  }));

  expect(apiMocks.getLatestTimetable).not.toHaveBeenCalled();
  await act(async () => {
    await result.current.runSmartSearch(query);
  });
  await waitFor(() => expect(result.current.timetableData?.search?.query).toBe(query));
  await waitFor(() => expect(result.current.isScraperRunning).toBe(false));
  await act(async () => {
    result.current.clearSmartSearch();
    await Promise.resolve();
  });
  await waitFor(() => expect(result.current.timetableData).toBeNull());
  await act(async () => {
    await result.current.runSmartSearch(query);
  });

  expect(apiMocks.searchTimetable).toHaveBeenCalledTimes(1);
  expect(result.current.timetableData?.search?.query).toBe(query);
});

test('a differently worded query for a fresh cached weekday never contacts Render', async () => {
  const firstQuery = 'BSSE7A timetable on Monday';
  const secondQuery = 'Software Project Management on Monday';
  const item = {
    semester: 'BS(SE)-7A',
    semester_display: 'BS(SE)-7A',
    course_title: 'Software Project Management',
    course_code: 'SEC 3603',
    schedule_day: 'Monday',
    time: '02:00 PM - 03:30 PM',
  };
  const serverResult = {
    ...makeSearchData(firstQuery, 'Found one class.', [item]),
    source_cache: {
      tier: 'firestore',
      snapshot_version: 1,
      requested_days: ['Monday'],
      refreshed_at_by_day: { Monday: '2099-01-01T00:00:00Z' },
      source_received_at_by_day: { Monday: '2099-01-01T00:00:00Z' },
      parser_version: 4,
      source_items: [item],
    },
  };
  apiMocks.searchTimetable.mockResolvedValue({ success: true, data: serverResult });

  const { result } = renderHook(() => useDashboardController({
    bootstrap: null,
    deleteAccount: vi.fn(),
    isAuthenticated: true,
    loading: false,
    logout: vi.fn(),
    user: { id: 'fresh-source-user', email: 'fresh-source@example.com' },
  }));

  await act(async () => { await result.current.runSmartSearch(firstQuery); });
  await act(async () => { result.current.clearSmartSearch(); await Promise.resolve(); });
  await act(async () => { await result.current.runSmartSearch(secondQuery); });

  expect(apiMocks.searchTimetable).toHaveBeenCalledTimes(1);
  expect(result.current.timetableData?.source_cache?.tier).toBe('browser');
  expect(result.current.timetableData?.items[0]?.course_title).toBe('Software Project Management');
});

test('a saved weekly snapshot remains usable past the old refresh boundary', async () => {
  const firstQuery = 'BSSE7A timetable on Monday';
  const secondQuery = 'Software Project Management on Monday';
  const item = {
    semester: 'BS(SE)-7A',
    course_title: 'Software Project Management',
    schedule_day: 'Monday',
  };
  const staleResult = {
    ...makeSearchData(firstQuery, 'Found one class.', [item]),
    source_cache: {
      tier: 'firestore',
      snapshot_version: 1,
      requested_days: ['Monday'],
      refreshed_at_by_day: { Monday: '2020-01-01T00:00:00Z' },
      source_received_at_by_day: { Monday: '2020-01-01T00:00:00Z' },
      parser_version: 4,
      source_items: [item],
    },
  };
  apiMocks.searchTimetable.mockImplementation(async (query: string) => ({
    success: true,
    data: { ...staleResult, search: { ...staleResult.search!, query } },
  }));

  const { result } = renderHook(() => useDashboardController({
    bootstrap: null,
    deleteAccount: vi.fn(),
    isAuthenticated: true,
    loading: false,
    logout: vi.fn(),
    user: { id: 'stale-source-user', email: 'stale-source@example.com' },
  }));

  await act(async () => { await result.current.runSmartSearch(firstQuery); });
  await act(async () => { result.current.clearSmartSearch(); await Promise.resolve(); });
  await act(async () => { await result.current.runSmartSearch(secondQuery); });

  expect(apiMocks.searchTimetable).toHaveBeenCalledTimes(1);
});

test('manual refresh bypasses saved answers and replaces the cached source', async () => {
  const query = 'BSSE7A classes on Monday';
  const oldItem = { semester_display: 'BS(SE)-7A', course_title: 'Old Class', schedule_day: 'Monday' };
  const newItem = { semester_display: 'BS(SE)-7A', course_title: 'New Class', schedule_day: 'Monday' };
  const oldResult = makeSearchData(query, 'Found old class.', [oldItem]);
  const newResult = makeSearchData(query, 'Found new class.', [newItem]);
  apiMocks.searchTimetable
    .mockResolvedValueOnce({ success: true, data: oldResult })
    .mockResolvedValueOnce({ success: true, data: newResult });

  const { result } = renderHook(() => useDashboardController({
    bootstrap: null, deleteAccount: vi.fn(), isAuthenticated: true,
    loading: false, logout: vi.fn(), user: { id: 'refresh-user', email: 'refresh-user@example.com' },
  }));
  await act(async () => { await result.current.runSmartSearch(query); });
  await act(async () => { await result.current.refreshTimetable(query); });
  expect(apiMocks.searchTimetable).toHaveBeenNthCalledWith(2, query, true);
  expect(result.current.timetableData?.items[0]?.course_title).toBe('New Class');

  await act(async () => { result.current.clearSmartSearch(); await Promise.resolve(); });
  await act(async () => { await result.current.runSmartSearch(query); });
  expect(apiMocks.searchTimetable).toHaveBeenCalledTimes(2);
  expect(result.current.timetableData?.items[0]?.course_title).toBe('New Class');
});
