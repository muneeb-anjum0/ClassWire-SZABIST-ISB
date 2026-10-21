import { expect, test } from 'vitest';
import { parseLocalQueryDays, searchCachedTimetable } from '../services/localTimetableSearch';

const items = [
  {
    semester_display: 'BS(SE)-7A', course_title: 'Software Project Management',
    course: 'SEC 3603 · (3,0)', course_code: 'SEC 3603', faculty: 'Muhammad Qasim',
    schedule_day: 'Monday', time: '02:00 PM - 03:30 PM',
  },
  {
    semester_display: 'BS(SE)-7A', course_title: 'Software Re-Engineering',
    course: 'SEC 3606 · (3,0)', course_code: 'SEC 3606', faculty: 'Awais Mahmood',
    schedule_day: 'Monday', time: '08:00 PM - 09:30 PM',
  },
  {
    semester_display: 'BS(SE)-5B', course_title: 'Software Construction and Development',
    course: 'SEC 3604 · (2,0)', course_code: 'SEC 3604', faculty: 'Iqra Yasmin',
    schedule_day: 'Monday', time: '05:00 PM - 06:00 PM',
  },
  {
    semester_display: 'BS(SE)-6A', course_title: 'Software Quality Engineering and Testing',
    course: 'SEC 3608 · (3,0)', course_code: 'SEC 3608', faculty: 'Arfa Asaf',
    schedule_day: 'Monday', time: '02:00 PM - 03:30 PM',
  },
  {
    semester_display: 'BS(CS)-3A', course_title: 'Human Computer Interaction and Computer Graphics',
    course: 'CSC 3106 · (2,0)', course_code: 'CSC 3106', faculty: 'Zahida Khalid',
    schedule_day: 'Wednesday', time: '02:00 PM - 03:00 PM',
  },
];

const source = {
  items,
  snapshot_version: 1,
  parser_version: 4,
  refreshed_at_by_day: { Monday: '2026-10-12T05:00:00Z' },
  source_received_at_by_day: { Monday: '2026-10-12T05:00:00Z' },
};

test('relative day parsing uses Karachi time', () => {
  expect(parseLocalQueryDays('what classes do I have today?', new Date('2026-10-11T20:30:00Z'))).toEqual(['Monday']);
});

test('fresh browser source builds a cross-section custom timetable locally', () => {
  const result = searchCachedTimetable(
    'I am in BSSE7A except Software Re-Engineering, taking Software Construction and Development theory with BSSE5B and Software Quality Engineering and Testing with BSSE6A on Monday',
    source,
    17,
    new Date('2026-10-14T05:00:00Z'),
  );

  expect(result.source_cache?.tier).toBe('browser');
  expect(result.items.map((item) => item.course_title)).toEqual([
    'Software Project Management',
    'Software Construction and Development',
    'Software Quality Engineering and Testing',
  ]);
  expect(result.search?.query_plan?.combination).toBe('union');
  expect(result.search?.conflict_count).toBe(1);
  expect(result.search?.conflicts?.[0].day).toBe('Monday');
});

test('faculty availability is calculated from the cached source', () => {
  const result = searchCachedTimetable(
    'When is sir Qasim free on Monday?',
    source,
    17,
    new Date('2026-10-14T05:00:00Z'),
  );

  expect(result.search?.intent).toBe('free_time');
  expect(result.search?.faculty_availability?.[0].faculty).toBe('Muhammad Qasim');
  expect(result.search?.free_slots.Monday).toContain('8:00 AM to 2:00 PM');
});

test('a section with an excluded course retains its other classes', () => {
  const result = searchCachedTimetable(
    'BSSE7A except Software Re-engineering', source, 17,
  );
  expect(result.items.map((item) => item.course_title)).toEqual(['Software Project Management']);
  expect(result.search?.conflicts).toEqual([]);
});

test('a full-week snapshot answers exclusions and two course-section additions', () => {
  const result = searchCachedTimetable(
    'im taking classes with BSSE7A except Software Re-engineering and taking Software Construction and Development theory with BSSE5B and Human Computer Interaction and Computer Graphics with BSCS3A',
    source,
    17,
  );
  expect(result.items.map((item) => `${item.semester_display}:${item.course_title}`)).toEqual([
    'BS(SE)-7A:Software Project Management',
    'BS(SE)-5B:Software Construction and Development',
    'BS(CS)-3A:Human Computer Interaction and Computer Graphics',
  ]);
  expect(result.search?.query_plan?.selection_scope?.course_section_pairs).toEqual([
    { section: 'BS(SE)-5B', kind: 'course', value: 'Software Construction and Development', class_types: ['theory'] },
    { section: 'BS(CS)-3A', kind: 'course', value: 'Human Computer Interaction and Computer Graphics', class_types: [] },
  ]);
});
