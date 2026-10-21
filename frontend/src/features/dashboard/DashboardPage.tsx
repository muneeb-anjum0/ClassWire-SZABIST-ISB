import StatusIndicator from '../../components/StatusIndicator/StatusIndicator';
import TimetableTable from '../../components/TimetableTable/TimetableTable';
import { useAuth } from '../../context/AuthContext';
import { verifiedRefreshDates } from '../../services/timetableCache';
import SmartSearch from './SmartSearch';
import { useDashboardController } from './useDashboardController';

export default function DashboardPage() {
  const auth = useAuth();
  const controller = useDashboardController(auth);
  const showStatus = controller.status !== 'idle' || controller.isBackendWaking || controller.isStatusToastClosing;
  const showAccountDomainWarning = Boolean(controller.accountDomainWarning);
  const hasSchedule = Boolean(
    controller.timetableData &&
    controller.timetableData.items &&
    controller.timetableData.items.length > 0,
  );
  const isCustomSchedule = controller.timetableData?.search?.query_plan?.combination === 'union';
  const visibleConflicts = isCustomSchedule
    ? controller.timetableData?.search?.conflicts
    : [];
  const statusIndicator = showStatus ? (
    <StatusIndicator
      status={
        controller.status === 'loading'
          ? 'loading'
          : controller.status === 'success'
          ? 'success'
          : controller.status === 'warning'
          ? 'warning'
          : controller.status === 'error'
          ? 'error'
          : 'success'
      }
      message={
        controller.isBackendWaking
          ? controller.message || 'Backend is waking up on Render. First request after inactivity can take about a minute.'
          : controller.message || 'Ready'
      }
      closing={controller.isStatusToastClosing}
      onDismiss={controller.dismissStatus}
      tone={controller.isBackendWaking ? 'backend-wake' : 'default'}
    />
  ) : null;
  const accountDomainWarning = showAccountDomainWarning ? (
    <StatusIndicator
      status="error"
      message={controller.accountDomainWarning}
    />
  ) : null;

  return (
    <div className="app-shell">
      <div className="app-shell__content">
        <main className="dashboard-main">
          <SmartSearch
            query={controller.searchQuery}
            setQuery={controller.setSearchQuery}
            onSearch={controller.runSmartSearch}
            onRefresh={controller.refreshTimetable}
            onClear={controller.clearSmartSearch}
            loading={controller.isScraperRunning}
            data={controller.timetableData}
            userEmail={controller.userEmail}
            accountRecentSearches={controller.accountRecentSearches}
            onSearchHistoryChange={controller.syncSearchHistory}
            onLogout={controller.handleLogoutClick}
            onCancelLogout={controller.cancelLogoutConfirm}
            onDeleteAccount={controller.deleteAccount}
            logoutConfirmArmed={controller.logoutConfirmArmed}
            theme={controller.theme}
            onThemeChange={controller.setTheme}
          />

          {(showAccountDomainWarning || showStatus) && !hasSchedule && (
            <div className="schedule-status-slot">
              {accountDomainWarning}
              {statusIndicator}
            </div>
          )}

          {hasSchedule && controller.timetableData && (
              <section
                className={`schedule-panel ${controller.timetableData.search ? 'schedule-panel--search-result' : ''}`}
                aria-labelledby="schedule-heading"
              >
                <div className="schedule-panel__header schedule-panel__header--striped">
                  <h2 id="schedule-heading">Class schedule</h2>
                  <p className="schedule-panel__meta">
                    <span>{controller.timetableData.for_day || 'Today'}</span>
                    <span>{controller.filteredItems.length} {controller.filteredItems.length === 1 ? 'class' : 'classes'}</span>
                  </p>
                </div>
                {accountDomainWarning}
                {statusIndicator}
                {isCustomSchedule && (controller.timetableData.search?.conflict_count || 0) > 0 && (
                  <div className="schedule-panel__conflicts" role="status">
                    {controller.timetableData.search?.conflict_count} timetable overlap{controller.timetableData.search?.conflict_count === 1 ? '' : 's'} detected in this custom schedule.
                  </div>
                )}
                <div className="timetable-container">
                  <TimetableTable
                    items={controller.filteredItems}
                    conflicts={visibleConflicts}
                    refreshedAtByDay={verifiedRefreshDates(controller.timetableData)}
                  />
                </div>
              </section>
            )}

        </main>

        <footer>
          <div className="app-footer">
            <span>&copy; {new Date().getFullYear()} ClassWire</span>
            <a href="/privacy">Privacy Policy</a>
            <a href="/terms">Terms of Service</a>
          </div>
        </footer>

      </div>
    </div>
  );
}
