import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowUp,
  Clock3,
  LogOut,
  Moon,
  RefreshCw,
  Search,
  Sun,
  Trash2,
  UserRound,
  X,
} from 'lucide-react';
import { TimetableData } from '../../types/api';
import { DashboardTheme } from './dashboardControllerTypes';
import {
  addRecentSearch,
  buildSmartSuggestions,
  parseRecentSearches,
  parseSuggestionHistory,
  rememberSuggestions,
  removeRecentSearch,
} from './smartSearchSuggestions';
import { isSzabistIslamabadEmail } from './utils';
import './smart-search.css';

type Props = {
  query: string;
  setQuery: (value: string) => void;
  onSearch: (query?: string) => void | Promise<unknown>;
  onRefresh: (query?: string) => void | Promise<unknown>;
  onClear: () => void;
  loading: boolean;
  data: TimetableData | null;
  userEmail?: string;
  accountRecentSearches?: string[];
  onSearchHistoryChange?: (searches: string[]) => void | Promise<unknown>;
  onLogout: () => void;
  onCancelLogout: () => void;
  onDeleteAccount?: () => Promise<void>;
  logoutConfirmArmed: boolean;
  theme: DashboardTheme;
  onThemeChange: (theme: DashboardTheme) => void;
};

const WEEKDAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const EMPTY_SEARCH_HISTORY: string[] = [];
const sameSearchHistory = (left: string[], right: string[]) =>
  left.length === right.length && left.every((value, index) => value === right[index]);

export default function SmartSearch({
  query,
  setQuery,
  onSearch,
  onRefresh,
  onClear,
  loading,
  data,
  userEmail,
  accountRecentSearches = EMPTY_SEARCH_HISTORY,
  onSearchHistoryChange,
  onLogout,
  onCancelLogout,
  onDeleteAccount,
  logoutConfirmArmed,
  theme,
  onThemeChange,
}: Props) {
  const [accountOpen, setAccountOpen] = useState(false);
  const [composerOpen, setComposerOpen] = useState(true);
  const [resultHidden, setResultHidden] = useState(true);
  const [deleteArmed, setDeleteArmed] = useState(false);
  const [isDeletingAccount, setIsDeletingAccount] = useState(false);
  const [deleteAccountError, setDeleteAccountError] = useState('');
  const [recentSearches, setRecentSearches] = useState<string[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const accountRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const suggestionHistoryRef = useRef<string[]>([]);
  const result = data?.search;
  const resultKey = `${result?.query || ''}|${result?.saved_at || ''}|${result?.answer || ''}`;
  const hasContent = Boolean(result || data?.items?.length);
  const availability = result?.faculty_availability || [];
  const accountConfirmation = logoutConfirmArmed ? 'logout' : deleteArmed ? 'delete' : null;
  const recentStorageKey = useMemo(
    () => `classwire:v3:recent-searches:${(userEmail || 'anonymous').toLocaleLowerCase()}`,
    [userEmail],
  );
  const suggestionStorageKey = useMemo(
    () => `classwire:v3:suggestion-history:${(userEmail || 'anonymous').toLocaleLowerCase()}`,
    [userEmail],
  );

  const persistRecentSearches = useCallback((searches: string[]) => {
    setRecentSearches(searches);
    try {
      window.localStorage.setItem(recentStorageKey, JSON.stringify(searches));
    } catch (error) {
      console.warn('Could not save recent timetable searches:', error);
    }
    void Promise.resolve(onSearchHistoryChange?.(searches)).catch((error) => {
      console.warn('Could not sync recent timetable searches:', error);
    });
  }, [onSearchHistoryChange, recentStorageKey]);

  const rememberSearch = useCallback((search: string) => {
    setRecentSearches((current) => {
      const next = addRecentSearch(current, search);
      if (sameSearchHistory(current, next)) return current;
      try {
        window.localStorage.setItem(recentStorageKey, JSON.stringify(next));
      } catch (error) {
        console.warn('Could not save recent timetable searches:', error);
      }
      void Promise.resolve(onSearchHistoryChange?.(next)).catch((error) => {
        console.warn('Could not sync recent timetable searches:', error);
      });
      return next;
    });
  }, [onSearchHistoryChange, recentStorageKey]);

  const rotateSuggestions = useCallback((historyOverride?: string[]) => {
    let history = historyOverride || suggestionHistoryRef.current;
    let next = buildSmartSuggestions(data, 4, Math.random, history);

    if (next.length < 4) {
      history = history.slice(-12);
      next = buildSmartSuggestions(data, 4, Math.random, history);
    }

    const updatedHistory = rememberSuggestions(history, next);
    suggestionHistoryRef.current = updatedHistory;
    setSuggestions(next);
    try {
      window.localStorage.setItem(suggestionStorageKey, JSON.stringify(updatedHistory));
    } catch (error) {
      console.warn('Could not save suggestion rotation history:', error);
    }
  }, [data, suggestionStorageKey]);

  const runQuery = useCallback((search: string) => {
    const cleaned = search.trim();
    if (loading || cleaned.length < 2) return;
    setQuery(cleaned);
    rememberSearch(cleaned);
    setResultHidden(true);
    setComposerOpen(false);
    onSearch(cleaned);
  }, [loading, onSearch, rememberSearch, setQuery]);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const visibleQuery = new FormData(event.currentTarget).get('timetable-query');
    runQuery(typeof visibleQuery === 'string' ? visibleQuery : query);
  };

  const refresh = () => {
    const cleaned = query.trim();
    if (loading || cleaned.length < 2) return;
    rememberSearch(cleaned);
    setResultHidden(true);
    setComposerOpen(false);
    onRefresh(cleaned);
  };

  const clearSearch = () => {
    if (loading) return;
    onClear();
    setResultHidden(true);
    setComposerOpen(true);
  };

  const confirmAccountDeletion = async () => {
    if (!onDeleteAccount || isDeletingAccount) return;

    setDeleteAccountError('');
    setIsDeletingAccount(true);
    try {
      await onDeleteAccount();
    } catch (error) {
      setDeleteAccountError(
        error instanceof Error ? error.message : 'Account data could not be deleted. Please try again.',
      );
      setIsDeletingAccount(false);
    }
  };

  const readableTime = (slot: string) => slot.replace(' – ', ' to ');
  const isAllDay = (freeSlots: string[]) => freeSlots.length === 1
    && freeSlots[0].replace(' – ', ' to ') === '8:00 AM to 9:30 PM';

  useEffect(() => {
    try {
      const localSearches = parseRecentSearches(window.localStorage.getItem(recentStorageKey));
      const merged = parseRecentSearches(JSON.stringify([
        ...accountRecentSearches,
        ...localSearches,
      ]));
      setRecentSearches(merged);
      window.localStorage.setItem(recentStorageKey, JSON.stringify(merged));
      if (JSON.stringify(merged) !== JSON.stringify(accountRecentSearches)) {
        void Promise.resolve(onSearchHistoryChange?.(merged)).catch((error) => {
          console.warn('Could not migrate recent timetable searches:', error);
        });
      }
    } catch {
      setRecentSearches([]);
    }
  }, [accountRecentSearches, onSearchHistoryChange, recentStorageKey]);

  useEffect(() => {
    let storedHistory: string[] = [];
    try {
      storedHistory = parseSuggestionHistory(window.localStorage.getItem(suggestionStorageKey));
    } catch {
      storedHistory = [];
    }
    suggestionHistoryRef.current = storedHistory;
    rotateSuggestions(storedHistory);
  }, [rotateSuggestions, suggestionStorageKey]);

  useEffect(() => {
    setResultHidden(true);
    if (result) setComposerOpen(false);
  }, [resultKey]);

  useEffect(() => {
    const restoredQuery = result?.query?.trim();
    if (restoredQuery) rememberSearch(restoredQuery);
  }, [rememberSearch, result?.query]);

  useEffect(() => {
    if (!accountOpen && !composerOpen) return undefined;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (accountOpen && !accountRef.current?.contains(target)) {
        setAccountOpen(false);
        onCancelLogout();
      }
      if (composerOpen && !composerRef.current?.contains(target)) setComposerOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setAccountOpen(false);
        onCancelLogout();
        setComposerOpen(false);
      }
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [accountOpen, composerOpen, onCancelLogout]);

  useEffect(() => {
    if (accountOpen || isDeletingAccount) return;
    setDeleteArmed(false);
    setDeleteAccountError('');
  }, [accountOpen, isDeletingAccount]);

  return <section className={`smart-search ${hasContent ? 'smart-search--compact' : 'smart-search--empty'}`}>
    <div className="smart-search__account" ref={accountRef}>
      <button
        type="button"
        className="smart-search__account-trigger"
        onClick={() => {
          if (accountOpen) onCancelLogout();
          setAccountOpen((open) => !open);
        }}
        aria-label="Open account menu"
        aria-expanded={accountOpen}
      ><UserRound aria-hidden="true" /></button>
      {accountOpen && <div className="smart-search__account-menu" role="menu">
        <div className="smart-search__account-email">
          <span className="smart-search__account-identity-icon"><UserRound aria-hidden="true" /></span>
          <span className="smart-search__account-identity-copy">
            <span>{isSzabistIslamabadEmail(userEmail) ? 'SZABIST Islamabad account' : 'Connected Google account'}</span>
            <strong>{userEmail || 'Google account'}</strong>
          </span>
        </div>
        <div className="smart-search__account-actions" role="presentation">
          <button
            type="button"
            role="menuitem"
            className="smart-search__account-action"
            aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            onClick={() => onThemeChange(theme === 'dark' ? 'light' : 'dark')}
          >
            <span className="smart-search__account-action-icon">
              {theme === 'dark' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
            </span>
            <span className="smart-search__account-action-copy">
              <strong>Appearance</strong>
              <small>{theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}</small>
            </span>
            <span className="smart-search__account-action-value">{theme === 'dark' ? 'Dark' : 'Light'}</span>
          </button>
          {!accountConfirmation && <button
            type="button"
            role="menuitem"
            aria-label="Sign out"
            className="smart-search__account-action"
            onClick={onLogout}
          >
            <span className="smart-search__account-action-icon"><LogOut aria-hidden="true" /></span>
            <span className="smart-search__account-action-copy">
              <strong>Sign out</strong>
              <small>End this browser session</small>
            </span>
          </button>}
          {!accountConfirmation && <div className="smart-search__account-divider" role="separator" />}
          {onDeleteAccount && !accountConfirmation && <button
            type="button"
            role="menuitem"
            aria-label="Delete account data"
            className="smart-search__account-action smart-search__account-action--delete"
            onClick={() => setDeleteArmed(true)}
          >
            <span className="smart-search__account-action-icon"><Trash2 aria-hidden="true" /></span>
            <span className="smart-search__account-action-copy">
              <strong>Delete account data</strong>
              <small>Remove saved ClassWire information</small>
            </span>
          </button>}
          {accountConfirmation && <div
            className={`smart-search__account-confirmation smart-search__account-confirmation--${accountConfirmation}`}
            role="alert"
          >
            <span className="smart-search__account-confirmation-header">
              <span className="smart-search__account-confirmation-icon">
                {accountConfirmation === 'logout' ? <LogOut aria-hidden="true" /> : <Trash2 aria-hidden="true" />}
              </span>
              <span>
                <strong>{accountConfirmation === 'logout' ? 'Sign out of ClassWire?' : 'Delete all account data?'}</strong>
                <small>
                  {accountConfirmation === 'logout'
                    ? 'Your saved data will remain available next time.'
                    : 'This permanently removes your timetable, settings, Gmail token, and search history.'}
                </small>
              </span>
            </span>
            {deleteAccountError && <p className="smart-search__account-confirmation-error" role="status">{deleteAccountError}</p>}
            <div className="smart-search__account-confirmation-actions">
              <button
                type="button"
                onClick={() => {
                  if (accountConfirmation === 'logout') onCancelLogout();
                  else setDeleteArmed(false);
                  setDeleteAccountError('');
                }}
                disabled={isDeletingAccount}
              >
                Cancel
              </button>
              <button
                type="button"
                className="smart-search__account-confirmation-submit"
                onClick={() => {
                  if (accountConfirmation === 'logout') onLogout();
                  else void confirmAccountDeletion();
                }}
                disabled={isDeletingAccount}
              >
                {isDeletingAccount && <RefreshCw className="smart-search__delete-spinner" aria-hidden="true" />}
                {isDeletingAccount
                  ? 'Deleting...'
                  : accountConfirmation === 'logout'
                    ? 'Sign out'
                    : 'Delete permanently'}
              </button>
            </div>
          </div>}
        </div>
      </div>}
    </div>

    {!hasContent && <header className="smart-search__intro">
      <h1>What’s on your schedule?</h1>
      <p>Ask about classes, faculty availability, courses, or sections.</p>
    </header>}

    <div className={`smart-search__composer ${composerOpen ? 'is-open' : ''}`} ref={composerRef}>
      <form onSubmit={submit} className="smart-search__form">
        <Search aria-hidden="true" />
        <input
          name="timetable-query"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onFocus={() => setComposerOpen(true)}
          placeholder="Ask about your timetable"
          aria-label="Search timetable"
          aria-expanded={composerOpen}
          aria-controls="smart-search-discovery"
          autoComplete="off"
        />
        {(query || hasContent) && <button
          type="button"
          className="smart-search__clear"
          onClick={clearSearch}
          disabled={loading}
          aria-label="Clear search and start over"
        ><X aria-hidden="true" /></button>}
        <div className={`smart-search__actions ${loading || query.trim().length < 2 ? 'is-disabled' : ''}`}>
          <button
            type="button"
            className="smart-search__refresh"
            onClick={refresh}
            disabled={loading || query.trim().length < 2}
            aria-label="Refresh the full weekly timetable from Gmail"
            title="Refresh the full weekly timetable from Gmail"
          >
            <RefreshCw aria-hidden="true" />
            <span>Refresh</span>
          </button>
          <button className="smart-search__submit" type="submit" disabled={loading || query.trim().length < 2} aria-label={loading ? 'Searching timetable' : 'Search timetable'}>
            {loading ? <span className="smart-search__loader" /> : <ArrowUp aria-hidden="true" />}
          </button>
        </div>
      </form>

      {composerOpen && <div className="smart-search__discovery" id="smart-search-discovery">
        {recentSearches.length > 0 && <section className="smart-search__discovery-section">
          <header>
            <span>Recent</span>
            <button type="button" onClick={() => persistRecentSearches([])}>Clear all</button>
          </header>
          <div className="smart-search__recent-list">
            {recentSearches.map((search) => <div className="smart-search__recent" key={search}>
              <button type="button" className="smart-search__recent-query" onClick={() => runQuery(search)}>
                <Clock3 aria-hidden="true" />
                <span>{search}</span>
              </button>
              <button
                type="button"
                className="smart-search__remove-recent"
                onClick={() => persistRecentSearches(removeRecentSearch(recentSearches, search))}
                aria-label={`Remove ${search} from recent searches`}
              ><X aria-hidden="true" /></button>
            </div>)}
          </div>
        </section>}

        <section className="smart-search__discovery-section smart-search__discovery-section--suggestions">
          <header>
            <span>Try asking</span>
            <button type="button" onClick={() => rotateSuggestions()}>
              <RefreshCw aria-hidden="true" />
              Refresh
            </button>
          </header>
          <div className="smart-search__suggestion-list">
            {suggestions.map((suggestion) => <button type="button" key={suggestion} onClick={() => runQuery(suggestion)}>
              <Search aria-hidden="true" />
              <span>{suggestion}</span>
            </button>)}
          </div>
        </section>
      </div>}
    </div>

    {result && <div
      key={resultKey}
      className={`smart-search__answer ${availability.length > 0 ? 'smart-search__answer--availability' : ''} ${resultHidden ? 'smart-search__answer--hidden' : 'smart-search__answer--visible'}`}
      role="status"
    >
      <div className="smart-search__answer-header">
        <div className="smart-search__answer-label">
          <span>Search result</span>
          {resultHidden && <span className="smart-search__answer-hidden-word"> hidden</span>}
        </div>
        <button
          type="button"
          className="smart-search__show-result"
          onClick={() => setResultHidden((hidden) => !hidden)}
          aria-label={resultHidden ? undefined : 'Hide search result'}
        >
          <span key={resultHidden ? 'show' : 'hide'}>{resultHidden ? 'Show' : 'Hide'}</span>
        </button>
      </div>
      <div className="smart-search__answer-body" aria-hidden={resultHidden}>
        <div className="smart-search__answer-body-inner">
          <div className="smart-search__answer-content">
            {availability.length > 0 ? <div className="smart-search__availability">
              {availability.map(({ faculty, slots }) => <section className="smart-search__faculty" key={faculty}>
                <header>
                  <div className="smart-search__faculty-identity">
                    <div>
                      <h3>{faculty}</h3>
                      <p>Free during university hours</p>
                    </div>
                  </div>
                  <span className="smart-search__faculty-day-count">
                    {Object.keys(slots).length} {Object.keys(slots).length === 1 ? 'day' : 'days'}
                  </span>
                </header>
                <div className="smart-search__days">
                  {Object.entries(slots)
                    .sort(([left], [right]) => WEEKDAY_ORDER.indexOf(left) - WEEKDAY_ORDER.indexOf(right))
                    .map(([day, freeSlots]) => {
                      const allDay = isAllDay(freeSlots);
                      const unavailable = freeSlots.length === 0;
                      return <div
                        className={`smart-search__day ${allDay ? 'smart-search__day--all-day' : ''} ${unavailable ? 'smart-search__day--unavailable' : ''}`}
                        key={`${faculty}-${day}`}
                      >
                        <div className="smart-search__day-label">
                          <strong>{day}</strong>
                          {unavailable && <small>Unavailable</small>}
                        </div>
                        {!unavailable
                          ? <div className="smart-search__slots">
                            {allDay
                              ? <span className="smart-search__slot smart-search__slot--all-day">
                                <b>All day</b>
                                <small>8:00 AM to 9:30 PM</small>
                                <em>Possible off day. Contact the teacher before visiting.</em>
                              </span>
                              : freeSlots.map((slot, index) => <span className="smart-search__slot" key={slot}>{freeSlots.length > 1 && <i>{index + 1}</i>}{readableTime(slot)}</span>)}
                          </div>
                          : <span className="smart-search__none" role="alert">Not available this day</span>}
                      </div>;
                    })}
                </div>
              </section>)}
            </div> : <p>{result.answer}</p>}
          </div>
        </div>
      </div>
    </div>}
  </section>;
}
