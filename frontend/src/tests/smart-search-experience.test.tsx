import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import SmartSearch from '../features/dashboard/SmartSearch';
import { TimetableData } from '../types/api';

const baseProps = {
  query: '',
  setQuery: vi.fn(),
  onSearch: vi.fn(),
  onRefresh: vi.fn(),
  onClear: vi.fn(),
  loading: false,
  userEmail: 'student@example.com',
  onLogout: vi.fn(),
  onCancelLogout: vi.fn(),
  logoutConfirmArmed: false,
  theme: 'light' as const,
  onThemeChange: vi.fn(),
};

test('the refresh control requests a new full-week Gmail snapshot', async () => {
  const onRefresh = vi.fn();
  const user = userEvent.setup();
  render(<SmartSearch {...baseProps} query="BSSE7A on Monday" onRefresh={onRefresh} data={null} />);

  await user.click(screen.getByRole('button', { name: 'Refresh the full weekly timetable from Gmail' }));
  expect(onRefresh).toHaveBeenCalledWith('BSSE7A on Monday');
  expect(baseProps.onSearch).not.toHaveBeenCalled();
});

test('recent searches can be reopened and removed', async () => {
  localStorage.setItem(
    'classwire:v3:recent-searches:student@example.com',
    JSON.stringify(['When is Zainab free?']),
  );
  const user = userEvent.setup();
  render(<SmartSearch {...baseProps} data={null} />);

  expect(await screen.findByText('When is Zainab free?')).toBeInTheDocument();
  expect(screen.getByText('Try asking').closest('section')).toHaveClass(
    'smart-search__discovery-section--suggestions',
  );
  await user.click(screen.getByLabelText('Remove When is Zainab free? from recent searches'));

  expect(screen.queryByText('When is Zainab free?')).not.toBeInTheDocument();
  expect(localStorage.getItem('classwire:v3:recent-searches:student@example.com')).toBe('[]');
});

test('account history is merged with legacy device history and synced back', async () => {
  localStorage.setItem(
    'classwire:v3:recent-searches:student@example.com',
    JSON.stringify(['Device-only search']),
  );
  const onSearchHistoryChange = vi.fn();

  render(
    <SmartSearch
      {...baseProps}
      data={null}
      accountRecentSearches={['Account search']}
      onSearchHistoryChange={onSearchHistoryChange}
    />,
  );

  expect(await screen.findByText('Account search')).toBeInTheDocument();
  expect(screen.getByText('Device-only search')).toBeInTheDocument();
  expect(onSearchHistoryChange).toHaveBeenCalledWith([
    'Account search',
    'Device-only search',
  ]);
});

test('a search answer starts hidden and can be restored without removing its data', async () => {
  const data = {
    for_day: 'Entire Week',
    for_date: '',
    query: 'When is Zainab free?',
    message_id: null,
    items: [],
    summary: {
      total_items: 0,
      semester_breakdown: {},
      unique_courses: 0,
      unique_faculty: 1,
    },
    search: {
      query: 'When is Zainab free?',
      answer: 'Zainab is free.',
      intent: 'free_time',
      days: ['Monday'],
      entities: {},
      free_slots: {},
      faculty_availability: [{
        faculty: 'Zainab Iftikhar Chaudhary',
        slots: {
          Monday: ['8:00 AM – 9:30 PM'],
          Tuesday: [],
        } as Record<string, string[]>,
      }, {
        faculty: 'Hamza Imran',
        slots: { Wednesday: ['10:00 AM – 12:00 PM'] } as Record<string, string[]>,
      }],
    },
  } satisfies TimetableData;
  const user = userEvent.setup();
  const { rerender } = render(<SmartSearch {...baseProps} data={data} />);

  const resultPanel = screen.getByText('Search result', { exact: false }).closest('.smart-search__answer');
  const resultBody = resultPanel?.querySelector('.smart-search__answer-body');
  expect(resultPanel).toHaveClass('smart-search__answer--hidden');
  expect(resultPanel).toHaveClass('smart-search__answer--availability');
  expect(resultPanel?.querySelector('.smart-search__faculty > header')).toBeInTheDocument();
  expect(resultPanel?.querySelector('.smart-search__faculty > header svg')).not.toBeInTheDocument();
  const facultySections = resultPanel?.querySelectorAll('.smart-search__faculty');
  expect(facultySections).toHaveLength(2);
  expect(facultySections?.[0].nextElementSibling).toBe(facultySections?.[1]);
  expect(screen.queryByText('Available')).not.toBeInTheDocument();
  expect(resultPanel?.querySelector('.smart-search__day--all-day')).toHaveTextContent(
    'Possible off day. Contact the teacher before visiting.',
  );
  expect(resultPanel?.querySelector('.smart-search__day--unavailable')).toHaveTextContent(
    'Not available this day',
  );
  expect(resultBody).toHaveAttribute('aria-hidden', 'true');

  const resultToggle = screen.getByRole('button', { name: 'Show' });
  await user.click(resultToggle);
  expect(screen.getByLabelText('Hide search result')).toBe(resultToggle);
  expect(resultPanel?.querySelector('.smart-search__answer-label')).toHaveTextContent('Search result');
  expect(resultPanel?.querySelector('.smart-search__answer-label')).not.toHaveTextContent('hidden');
  expect(screen.getByText('Zainab Iftikhar Chaudhary')).toBeInTheDocument();
  expect(resultPanel).toHaveClass('smart-search__answer--visible');
  expect(resultBody).toHaveAttribute('aria-hidden', 'false');
  expect(screen.getByRole('alert')).toHaveTextContent('Not available this day');

  rerender(<SmartSearch
    {...baseProps}
    data={{
      ...data,
      search: {
        ...data.search,
        query: 'When is Zainab free on Tuesday?',
        answer: 'Updated availability.',
      },
    }}
  />);

  const nextResultPanel = screen.getByText('Search result', { exact: false }).closest('.smart-search__answer');
  expect(nextResultPanel).toHaveClass('smart-search__answer--hidden');
  expect(nextResultPanel?.querySelector('.smart-search__answer-body')).toHaveAttribute('aria-hidden', 'true');
});

test('the composer clear button starts a new centered search', async () => {
  const onClear = vi.fn();
  const user = userEvent.setup();

  render(<SmartSearch {...baseProps} query="BSSE7A timetable Monday" onClear={onClear} data={null} />);

  await user.click(screen.getByRole('button', { name: 'Clear search and start over' }));

  expect(onClear).toHaveBeenCalledOnce();
});

test('account deletion requires explicit confirmation and shows request progress', async () => {
  let finishDeletion!: () => void;
  const onDeleteAccount = vi.fn(() => new Promise<void>((resolve) => {
    finishDeletion = resolve;
  }));
  const user = userEvent.setup();
  render(<SmartSearch {...baseProps} data={null} onDeleteAccount={onDeleteAccount} />);

  await user.click(screen.getByRole('button', { name: 'Open account menu' }));
  await user.click(screen.getByRole('menuitem', { name: 'Delete account data' }));

  expect(screen.getByText('Delete all account data?')).toBeInTheDocument();
  expect(onDeleteAccount).not.toHaveBeenCalled();

  await user.click(screen.getByRole('button', { name: 'Delete permanently' }));
  expect(onDeleteAccount).toHaveBeenCalledOnce();
  expect(screen.getByRole('button', { name: 'Deleting...' })).toBeDisabled();

  finishDeletion();
});

test('sign out uses the same explicit confirmation tray and can be cancelled', async () => {
  const onLogout = vi.fn();
  const onCancelLogout = vi.fn();
  const user = userEvent.setup();
  const { rerender } = render(
    <SmartSearch
      {...baseProps}
      data={null}
      onLogout={onLogout}
      onCancelLogout={onCancelLogout}
    />,
  );

  await user.click(screen.getByRole('button', { name: 'Open account menu' }));
  await user.click(screen.getByRole('menuitem', { name: 'Sign out' }));
  expect(onLogout).toHaveBeenCalledOnce();

  rerender(
    <SmartSearch
      {...baseProps}
      data={null}
      onLogout={onLogout}
      onCancelLogout={onCancelLogout}
      logoutConfirmArmed
    />,
  );

  expect(screen.getByText('Sign out of ClassWire?')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(onCancelLogout).toHaveBeenCalledOnce();
});
