import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiService, getApiErrorMessage } from '../../services/api';
import {
  clearSearchResultCache,
  deleteTimetableCache,
  readTimetableCache,
  readSearchResultCache,
  readTimetableSourceCache,
  isUsableWeeklySource,
  writeTimetableCache,
} from '../../services/timetableCache';
import { searchCachedTimetable } from '../../services/localTimetableSearch';
import { TimetableData } from '../../types/api';
import { DashboardAuthState } from './dashboardControllerTypes';
import { useDashboardStatusToast } from './useDashboardStatusToast';
import { useDashboardUiState } from './useDashboardUiState';
import { expandSocialSciencesSemesterItems, isSzabistIslamabadEmail } from './utils';

const SEARCH_PARSER_VERSION = 17;

export const useDashboardController = ({
  bootstrap: authBootstrap,
  deleteAccount,
  isAuthenticated,
  loading,
  logout,
  user,
}: DashboardAuthState) => {
  const ui = useDashboardUiState(logout);
  const statusToast = useDashboardStatusToast();
  const showStatus = statusToast.showStatus;
  const [timetableData, setTimetableData] = useState<TimetableData | null>(null);
  const [isScraperRunning, setIsScraperRunning] = useState(false);
  const [operationInProgress, setOperationInProgress] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const hydratedUser = useRef<string | null>(null);
  const bootstrappedUser = useRef<string | null>(null);
  const historySyncQueue = useRef<Promise<unknown>>(Promise.resolve());
  const pendingSearchHistory = useRef<string[] | null>(null);
  const dataRequestSequence = useRef(0);
  const searchInFlight = useRef(false);
  const accountRecentSearches = useMemo(
    () => authBootstrap?.search_history || [],
    [authBootstrap?.search_history],
  );

  const syncSearchHistory = useCallback(async (searches: string[]) => {
    if (!isAuthenticated) return;
    // Search-history writes must not be the reason a sleeping backend wakes.
    // Keep the newest account state queued and flush it alongside the next
    // server-required timetable refresh.
    pendingSearchHistory.current = searches;
  }, [isAuthenticated]);

  const flushSearchHistory = useCallback(async () => {
    const searches = pendingSearchHistory.current;
    if (!isAuthenticated || !searches) return;
    pendingSearchHistory.current = null;
    historySyncQueue.current = historySyncQueue.current
      .catch(() => undefined)
      .then(() => apiService.saveSearchHistory(searches));
    try {
      await historySyncQueue.current;
    } catch {
      pendingSearchHistory.current = searches;
    }
  }, [isAuthenticated]);

  const filteredItems = useMemo(
    () => expandSocialSciencesSemesterItems(timetableData?.items || []),
    [timetableData],
  );
  const accountDomainWarning = isAuthenticated &&
    !loading &&
    !isSzabistIslamabadEmail(user?.email)
      ? 'You are signed in with a non-SZABIST account. Use your @szabist-isb.pk Google account to access SZABIST timetable emails.'
      : '';

  const readCachedTimetable = async (): Promise<TimetableData | null> => {
    const cached = await readTimetableCache(user?.email);
    if (cached?.search && cached.search.parser_version !== SEARCH_PARSER_VERSION) return null;
    return cached;
  };

  const readSavedSearch = async (): Promise<TimetableData | null> => {
    const cached = await readTimetableCache(user?.email);
    return cached?.search?.query && cached.search.parser_version === SEARCH_PARSER_VERSION
      ? cached
      : null;
  };

  const applySuccessfulTimetable = (
    data: TimetableData,
    message?: string,
    silent = false,
    status: 'success' | 'warning' = 'success',
  ) => {
    setTimetableData(data);
    void writeTimetableCache(user?.email, data);
    if (!silent) showStatus(status, message || 'Data loaded successfully');
  };

  const runSmartSearch = async (query = searchQuery, forceRefresh = false) => {
    const cleaned = query.trim();
    if (cleaned.length < 2 || searchInFlight.current || isScraperRunning) return;

    const requestSequence = ++dataRequestSequence.current;
    searchInFlight.current = true;
    try {
      setSearchQuery(cleaned);
      if (!forceRefresh) {
        const cachedResult = await readSearchResultCache(user?.email, cleaned);
        if (
          cachedResult?.search?.parser_version === SEARCH_PARSER_VERSION
          && cachedResult.search.query?.trim().toLocaleLowerCase() === cleaned.toLocaleLowerCase()
        ) {
          applySuccessfulTimetable(cachedResult, 'Loaded instantly from this device', false);
          return;
        }
        const cachedSource = await readTimetableSourceCache(user?.email);
        if (cachedSource && isUsableWeeklySource(cachedSource)) {
          const localResult = searchCachedTimetable(
            cleaned,
            cachedSource,
            SEARCH_PARSER_VERSION,
          );
          applySuccessfulTimetable(localResult, 'Loaded instantly from your saved timetable', false,
            localResult.search?.recognized === false ? 'warning' : 'success');
          return;
        }
      }
      if (!forceRefresh) setTimetableData(null);
      setIsScraperRunning(true);
      setOperationInProgress(true);
      showStatus('loading', forceRefresh ? 'Refreshing the full week from Gmail...' : 'Searching your timetable...');
      const response = await apiService.searchTimetable(cleaned, forceRefresh);
      if (requestSequence !== dataRequestSequence.current) return;
      if (!response.success || !response.data) throw new Error(response.error || 'Search failed');
      if (response.data.search?.query?.trim() !== cleaned) {
        throw new Error('The search response did not match your question. Please try again.');
      }
      if (forceRefresh) await clearSearchResultCache(user?.email);
      applySuccessfulTimetable(
        response.data,
        forceRefresh ? 'Checked Gmail and updated your saved weekly timetable.' : response.message,
        false,
        response.data.search?.recognized === false ? 'warning' : 'success',
      );
      void flushSearchHistory();
    } catch (error) {
      if (requestSequence !== dataRequestSequence.current) return;
      if (!forceRefresh) setTimetableData(null);
      const apiMessage = getApiErrorMessage(error);
      showStatus('error', apiMessage || (error instanceof Error ? error.message : 'Search failed'));
    } finally {
      searchInFlight.current = false;
      if (requestSequence === dataRequestSequence.current) {
        setIsScraperRunning(false);
        setOperationInProgress(false);
      }
    }
  };

  const refreshTimetable = (query = searchQuery) => runSmartSearch(query, true);

  const clearSmartSearch = () => {
    if (isScraperRunning) return;
    dataRequestSequence.current += 1;
    setSearchQuery('');
    setTimetableData(null);
    void deleteTimetableCache(user?.email, false);
    statusToast.setStatus('idle');
    statusToast.setMessage('');
  };

  const handleDeleteAccount = async () => {
    try {
      showStatus('loading', 'Deleting your ClassWire data...');
      await deleteAccount();
    } catch (error) {
      showStatus('error', error instanceof Error ? error.message : 'Could not delete account data');
      throw error;
    }
  };

  useEffect(() => {
    const userKey = user?.email?.trim().toLowerCase();
    if (!isAuthenticated || !userKey || hydratedUser.current === userKey) return;
    hydratedUser.current = userKey;

    void readCachedTimetable().then((cached) => {
      if (!cached || hydratedUser.current !== userKey) return;
      setTimetableData(cached);
      setSearchQuery(cached.search?.query?.trim() || '');
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated, user?.email]);

  useEffect(() => {
    const userKey = user?.email?.trim().toLowerCase();
    if (!isAuthenticated || loading || !userKey || bootstrappedUser.current === userKey) return;
    bootstrappedUser.current = userKey;

    void (async () => {
      const cached = await readCachedTimetable();
      if (authBootstrap?.user?.email.toLowerCase() === userKey) {
        if (authBootstrap.timetable) {
          const localSearch = await readSavedSearch();
          const restoredData = authBootstrap.timetable.search
            ? authBootstrap.timetable
            : (localSearch || authBootstrap.timetable);
          setSearchQuery(restoredData.search?.query?.trim() || '');
          applySuccessfulTimetable(restoredData, undefined, true);
        }
        return;
      }
      // Do not wake Render merely to restore state. A cached browser paints
      // immediately; a browser without a weekly source waits for a search.
      if (cached) return;
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authBootstrap, isAuthenticated, loading, user?.email]);

  return {
    accountDomainWarning,
    accountRecentSearches,
    cancelLogoutConfirm: ui.cancelLogoutConfirm,
    clearSmartSearch,
    deleteAccount: handleDeleteAccount,
    dismissStatus: statusToast.dismissStatus,
    filteredItems,
    handleLogoutClick: ui.handleLogoutClick,
    isBackendWaking: statusToast.isBackendWaking,
    isScraperRunning,
    isStatusToastClosing: statusToast.isStatusToastClosing,
    logoutConfirmArmed: ui.logoutConfirmArmed,
    message: statusToast.message,
    runSmartSearch,
    refreshTimetable,
    syncSearchHistory,
    searchQuery,
    setSearchQuery,
    setTheme: ui.setTheme,
    status: statusToast.status,
    theme: ui.theme,
    timetableData,
    userEmail: user?.email,
  };
};
