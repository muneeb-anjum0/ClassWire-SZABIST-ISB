"""Backend tests for ClassWire."""

import json
import gzip
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app import (
    app,
    build_auth_handoff_redirect_page,
    get_user_from_request,
    oauth_handoff_store,
)
from core.app_support import compress_large_json_response


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def mock_user():
    return {
        'id': 'test-user-id',
        'email': 'test@example.com',
        'created_at': datetime.now().isoformat(),
    }


@pytest.fixture
def mock_store():
    mock = MagicMock()
    with patch('app.store', new=mock):
        yield mock


class TestHealthEndpoint:
    def test_health_check_success(self, client):
        response = client.get('/api/health')
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data['status'] == 'healthy'
        assert 'timestamp' in data
        assert data['config_loaded'] is True
        assert data['revision']
        assert 'firestore_connected' not in data

    def test_health_check_exposes_the_render_release_revision(self, client):
        with patch.dict('os.environ', {'RENDER_GIT_COMMIT': '1234567890abcdef'}):
            response = client.get('/api/health')

        assert response.get_json()['revision'] == '1234567890ab'

    def test_large_json_responses_are_gzipped_when_supported(self):
        with app.test_request_context('/', headers={'Accept-Encoding': 'gzip, deflate'}):
            response = app.json.response({'payload': 'x' * 5000})
            original_size = len(response.get_data())
            compressed = compress_large_json_response(response)

        assert compressed.headers['Content-Encoding'] == 'gzip'
        assert len(compressed.get_data()) < original_size
        assert json.loads(gzip.decompress(compressed.get_data()))['payload'] == 'x' * 5000

    def test_request_includes_trace_and_server_timing_headers(self, client):
        response = client.get('/api/health')
        assert response.headers['X-Request-ID']
        assert response.headers['Server-Timing'].startswith('app;dur=')


class TestBootstrapEndpoint:
    def test_logged_out_bootstrap_returns_an_explicit_guest_state(self, client):
        response = client.get('/api/bootstrap')

        assert response.status_code == 200
        assert response.get_json()['authenticated'] is False
        assert response.get_json()['user'] is None

    def test_bootstrap_returns_identity_and_cached_timetable(self, client, mock_store, mock_user):
        mock_store.get_or_create_user.return_value = mock_user
        mock_store.get_bootstrap_data.return_value = {
            'timetable': {'items': [], 'for_day': 'Entire Week'},
            'last_update': '2026-09-22T10:00:00Z',
            'search_history': ['BSSE7A on Monday'],
        }
        response = client.get('/api/bootstrap', headers={'X-User-Email': mock_user['email']})
        assert response.status_code == 200
        data = response.get_json()
        assert data['user']['email'] == mock_user['email']
        assert data['authenticated'] is True
        assert 'config' not in data
        assert data['timetable']['for_day'] == 'Entire Week'
        assert data['search_history'] == ['BSSE7A on Monday']

    def test_account_search_history_can_be_read_and_replaced(self, client, mock_store, mock_user):
        mock_store.get_or_create_user.return_value = mock_user
        mock_store.get_search_history.return_value = ['When is Wahab free?']
        mock_store.save_search_history.return_value = ['BSSE7A Monday', 'When is Wahab free?']

        read_response = client.get(
            '/api/search-history',
            headers={'X-User-Email': mock_user['email']},
        )
        write_response = client.put(
            '/api/search-history',
            json={'searches': ['BSSE7A Monday', 'When is Wahab free?']},
            headers={'X-User-Email': mock_user['email']},
        )

        assert read_response.get_json()['searches'] == ['When is Wahab free?']
        assert write_response.get_json()['searches'] == ['BSSE7A Monday', 'When is Wahab free?']
        mock_store.save_search_history.assert_called_once_with(
            mock_user['id'],
            ['BSSE7A Monday', 'When is Wahab free?'],
        )

    def test_metrics_require_automation_secret(self, client):
        response = client.get('/api/metrics')
        assert response.status_code == 401


class TestAccountDeletion:
    @patch('requests.post')
    def test_deletion_revokes_google_token_and_removes_all_user_data(self, revoke, client, mock_store, mock_user):
        mock_store.get_or_create_user.return_value = mock_user
        mock_store.get_user_tokens.return_value = {'refresh_token': 'refresh-token'}
        mock_store.delete_user_data.return_value = True
        response = client.delete('/api/account', headers={'X-User-Email': mock_user['email']})
        assert response.status_code == 200
        assert response.get_json() == {'success': True, 'message': 'Account data deleted'}
        revoke.assert_called_once()
        mock_store.delete_user_data.assert_called_once_with(mock_user['id'])

    def test_deletion_continues_when_stored_google_token_cannot_be_read(self, client, mock_store, mock_user):
        mock_store.get_or_create_user.return_value = mock_user
        mock_store.get_user_tokens.side_effect = ValueError('Unreadable token')
        mock_store.delete_user_data.return_value = True

        response = client.delete('/api/account', headers={'X-User-Email': mock_user['email']})

        assert response.status_code == 200
        mock_store.delete_user_data.assert_called_once_with(mock_user['id'])


class TestAuthentication:
    def test_logged_out_session_check_is_not_an_error_response(self, client):
        response = client.get('/api/auth/session')

        assert response.status_code == 200
        assert response.get_json() == {
            'success': True,
            'authenticated': False,
            'user': None,
        }

    def test_oauth_handoff_creates_a_session_and_is_single_use(self, client):
        oauth_handoff_store.store(
            'one-time-handoff',
            user_id='user-123',
            user_email='Student@SZABIST-ISB.PK',
        )

        response = client.post('/api/auth/handoff', json={'token': 'one-time-handoff'})
        session_response = client.get('/api/auth/session')
        replay_response = client.post('/api/auth/handoff', json={'token': 'one-time-handoff'})

        assert response.status_code == 200
        assert response.get_json()['user'] == {
            'id': 'user-123',
            'email': 'student@szabist-isb.pk',
        }
        assert session_response.get_json()['authenticated'] is True
        assert replay_response.status_code == 401

    def test_oauth_handoff_redirect_keeps_the_token_out_of_the_request_query(self):
        page = build_auth_handoff_redirect_page(
            'https://szabist-islamabad-search.vercel.app',
            {'auth': 'success', 'handoff': 'short-lived-token'},
            'Redirecting',
        )

        assert 'https://szabist-islamabad-search.vercel.app/#auth=success&amp;handoff=short-lived-token' in page
        assert 'https://szabist-islamabad-search.vercel.app/?auth=success' not in page

    def test_get_user_from_request_valid_header(self, mock_store, mock_user):
        mock_store.get_or_create_user.return_value = mock_user

        with app.test_request_context('/', headers={'X-User-Email': 'test@example.com'}):
            user, error, status = get_user_from_request()

            assert user == mock_user
            assert error is None
            assert status is None

    def test_get_user_from_request_no_email(self):
        with app.test_request_context('/'):
            user, error, status = get_user_from_request()

            assert user is None
            assert status == 401

    def test_get_user_from_request_invalid_email(self, mock_store, mock_user):
        mock_store.get_or_create_user.return_value = mock_user

        with app.test_request_context('/', headers={'X-User-Email': 'invalid-email'}):
            user, error, status = get_user_from_request()

            assert user is not None
            assert error is None
            assert status is None


class TestAuthenticationBoundary:
    def test_identity_header_is_ignored_outside_tests(self, client):
        original_testing = app.testing
        app.testing = False
        try:
            response = client.get('/api/bootstrap', headers={'X-User-Email': 'spoofed@example.com'})
        finally:
            app.testing = original_testing

        assert response.status_code == 200
        assert response.get_json()['authenticated'] is False
        assert response.get_json()['user'] is None

class TestDailyEmailToggle:
    def test_enable_daily_email_requires_personal_email(self, client, mock_store, mock_user):
        mock_store.get_or_create_user.return_value = mock_user
        mock_store.get_user_settings.return_value = {'personal_email': ''}

        response = client.post(
            '/api/config/daily-email-enabled',
            json={'daily_email_enabled': True},
            headers={'X-User-Email': 'test@example.com'},
        )

        assert response.status_code == 400
        assert response.get_json()['success'] is False
        mock_store.save_user_settings.assert_not_called()

    def test_enable_daily_email_saves_true(self, client, mock_store, mock_user):
        mock_store.get_or_create_user.return_value = mock_user
        settings = {'personal_email': 'personal@example.com', 'daily_email_enabled': False}
        mock_store.get_user_settings.return_value = settings
        mock_store.save_user_settings.return_value = True

        response = client.post(
            '/api/config/daily-email-enabled',
            json={'daily_email_enabled': True},
            headers={'X-User-Email': 'test@example.com'},
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data['success'] is True
        assert data['daily_email_enabled'] is True
        mock_store.save_user_settings.assert_called_once()
        assert mock_store.save_user_settings.call_args.args[1]['daily_email_enabled'] is True

    def test_disable_daily_email_saves_false(self, client, mock_store, mock_user):
        mock_store.get_or_create_user.return_value = mock_user
        settings = {'personal_email': 'personal@example.com', 'daily_email_enabled': True}
        mock_store.get_user_settings.return_value = settings
        mock_store.save_user_settings.return_value = True

        response = client.post(
            '/api/config/daily-email-enabled',
            json={'daily_email_enabled': False},
            headers={'X-User-Email': 'test@example.com'},
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data['success'] is True
        assert data['daily_email_enabled'] is False
        mock_store.save_user_settings.assert_called_once()
        assert mock_store.save_user_settings.call_args.args[1]['daily_email_enabled'] is False


class TestSearchEndpoint:
    @patch('app.run_once')
    def test_first_search_scrapes_full_week_then_next_query_reuses_snapshot(self, mock_run_once, client, mock_store):
        user = {'id': 'weekly-snapshot-user', 'email': 'weekly@example.com'}
        source = {
            'snapshot_version': 1, 'for_day': 'Entire Week', 'items': [
                {'schedule_day': 'Monday', 'semester_display': 'BS(SE)-7A',
                 'course_title': 'Software Project Management', 'course_code': 'SEC 3603',
                 'course': 'SEC 3603 Software Project Management (3,0)', 'time': '02:00 PM - 03:30 PM'},
                {'schedule_day': 'Tuesday', 'semester_display': 'BS(SE)-7A',
                 'course_title': 'Digital Image Processing', 'course_code': 'SEC 4515',
                 'course': 'SEC 4515 Digital Image Processing (3,0)', 'time': '03:30 PM - 05:00 PM'},
            ],
        }
        mock_store.get_or_create_user.return_value = user
        mock_store.get_user_settings.return_value = {}
        mock_store.get_search_source_cache.return_value = None
        mock_store.save_search_source_cache.return_value = True
        mock_run_once.return_value = {'success': True, 'data': source}

        first = client.post('/api/search', json={'query': 'BSSE7A on Monday'}, headers={'X-User-Email': user['email']})
        second = client.post('/api/search', json={'query': 'BSSE7A on Tuesday'}, headers={'X-User-Email': user['email']})

        assert first.status_code == second.status_code == 200
        assert mock_run_once.call_count == 1
        assert mock_run_once.call_args.kwargs['user_settings']['_refresh_days'] == [
            'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday',
        ]
        assert second.get_json()['data']['items'][0]['course_title'] == 'Digital Image Processing'

    @patch('app.run_once')
    def test_manual_refresh_replaces_the_whole_week_even_for_a_one_day_query(self, mock_run_once, client, mock_store):
        user = {'id': 'manual-weekly-refresh-user', 'email': 'manual@example.com'}
        old_source = {
            'snapshot_version': 1, 'parser_version': 2,
            'for_day': 'Entire Week', 'message_ids': ['old-monday'],
            'items': [{'schedule_day': 'Monday', 'course_title': 'Old Monday Class'}],
        }
        new_source = {
            'snapshot_version': 1, 'parser_version': 2,
            'for_day': 'Entire Week', 'message_ids': ['new-monday', 'new-tuesday'],
            'items': [
                {'schedule_day': 'Monday', 'course_title': 'New Monday Class'},
                {'schedule_day': 'Tuesday', 'course_title': 'New Tuesday Class'},
            ],
        }
        mock_store.get_or_create_user.return_value = user
        mock_store.get_search_source_cache.return_value = old_source
        mock_store.get_user_settings.return_value = {}
        mock_store.save_search_source_cache.return_value = True
        mock_run_once.return_value = {'success': True, 'data': new_source}
        headers = {'X-User-Email': user['email']}

        refreshed = client.post('/api/search', json={
            'query': 'classes on Monday', 'force_refresh': True,
        }, headers=headers)
        following = client.post('/api/search', json={'query': 'classes on Tuesday'}, headers=headers)

        assert refreshed.status_code == following.status_code == 200
        assert mock_run_once.call_count == 1
        assert mock_run_once.call_args.kwargs['user_settings']['_previous_source'] == old_source
        assert mock_run_once.call_args.kwargs['user_settings']['_refresh_days'] == [
            'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday',
        ]
        mock_store.save_search_source_cache.assert_called_once_with(user['id'], new_source)
        assert following.get_json()['data']['source_cache']['source_items'] == new_source['items']

    @patch('app.run_once')
    def test_search_returns_browser_source_snapshot_without_nesting_items_in_search_metadata(self, mock_run_once, client, mock_store, mock_user):
        item = {
            'schedule_day': 'Monday',
            'semester_display': 'BS(SE)-7A',
            'course': 'SEC 3603 Software Project Management (3,0)',
            'course_code': 'SEC 3603',
            'course_title': 'Software Project Management',
            'faculty': 'Ada Lovelace',
            'room': '204',
            'time': '02:00 PM - 03:30 PM',
            'campus': 'Main Campus',
        }
        source = {
            'for_day': 'Entire Week',
            'for_date': '2026-09-19',
            'query': 'gmail query',
            'message_id': 'message-1',
            'message_ids': ['message-1'],
            'items': [item],
            'summary': {},
        }
        mock_store.get_or_create_user.return_value = mock_user
        mock_store.get_user_settings.return_value = {}
        mock_store.get_search_source_cache.return_value = None
        mock_store.save_search_source_cache.return_value = True
        mock_store.save_timetable_cache.return_value = True
        mock_run_once.return_value = {'success': True, 'data': source}

        response = client.post(
            '/api/search',
            json={'query': 'When does Ada Lovelace have classes?', 'force_refresh': True},
            headers={'X-User-Email': 'test@example.com'},
        )

        assert response.status_code == 200
        data = response.get_json()['data']
        assert data['items'] == [item]
        assert 'items' not in data['search']
        assert data['source_cache']['source_items'] == [item]
        mock_store.save_search_source_cache.assert_called_once_with(mock_user['id'], source)
        mock_store.save_timetable_cache.assert_not_called()

    def test_repeated_search_reuses_the_parsed_result_without_a_firestore_write(self, client, mock_store):
        from scraper.smart_search import search_timetable as real_search_timetable

        user = {'id': 'repeated-search-user', 'email': 'repeat@example.com'}
        source = {
            'items': [{
                'schedule_day': 'Monday',
                'semester_display': 'BS(SE)-7A',
                'course': 'SEC 3603 Software Project Management (3,0)',
                'course_code': 'SEC 3603',
                'course_title': 'Software Project Management',
                'faculty': 'Muhammad Qasim',
                'time': '02:00 PM - 03:30 PM',
            }],
            'for_day': 'Entire Week',
            'snapshot_version': 1,
            'parser_version': 2,
            'summary': {},
            'refreshed_at_by_day': {
                'Monday': datetime.now(timezone.utc).isoformat(),
            },
        }
        mock_store.get_or_create_user.return_value = user
        mock_store.get_search_source_cache.return_value = source

        with patch('scraper.smart_search.search_timetable', wraps=real_search_timetable) as parser:
            first = client.post(
                '/api/search',
                json={'query': 'BSSE7A classes on Monday'},
                headers={'X-User-Email': user['email']},
            )
            second = client.post(
                '/api/search',
                json={'query': '  BSSE7A   classes on MONDAY  '},
                headers={'X-User-Email': user['email']},
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert parser.call_count == 1
        assert first.get_json()['data']['items'] == second.get_json()['data']['items']
        mock_store.save_timetable_cache.assert_not_called()

    @patch('app.run_once')
    def test_search_api_preserves_course_section_pairs_in_a_custom_schedule(self, mock_run_once, client, mock_store):
        user = {'id': 'paired-custom-schedule-user', 'email': 'paired@example.com'}
        items = [
            {'schedule_day': 'Monday', 'semester_display': 'BS(SE)-7A', 'course_code': 'SEC 7001', 'course_title': 'Core Seven A', 'course': 'SEC 7001 Core Seven A (3,0)', 'time': '08:00 AM - 09:30 AM'},
            {'schedule_day': 'Tuesday', 'semester_display': 'BS(SE)-7A', 'course_code': 'SEC 7002', 'course_title': 'Another Core Seven A', 'course': 'SEC 7002 Another Core Seven A (3,0)', 'time': '09:30 AM - 11:00 AM'},
            {'schedule_day': 'Wednesday', 'semester_display': 'BS(SE)-5A', 'course_code': 'SEC 3604', 'course_title': 'Software Construction and Development', 'course': 'SEC 3604 Software Construction and Development (2,0)', 'time': '02:00 PM - 03:00 PM'},
            {'schedule_day': 'Wednesday', 'semester_display': 'BS(SE)-5B', 'course_code': 'SEC 3604', 'course_title': 'Software Construction and Development', 'course': 'SEC 3604 Software Construction and Development (2,0)', 'time': '04:00 PM - 05:00 PM'},
            {'schedule_day': 'Thursday', 'semester_display': 'BS(SE)-6A', 'course_code': 'SEC 3608', 'course_title': 'Software Quality Engineering and Testing', 'course': 'SEC 3608 Software Quality Engineering and Testing (3,0)', 'time': '02:00 PM - 03:30 PM'},
            {'schedule_day': 'Friday', 'semester_display': 'BS(SE)-6B', 'course_code': 'SEC 3608', 'course_title': 'Software Quality Engineering and Testing', 'course': 'SEC 3608 Software Quality Engineering and Testing (3,0)', 'time': '06:00 PM - 07:30 PM'},
        ]
        source = {'items': items, 'for_day': 'Entire Week', 'summary': {}}
        mock_store.get_or_create_user.return_value = user
        mock_store.get_user_settings.return_value = {}
        mock_store.get_search_source_cache.return_value = None
        mock_store.save_search_source_cache.return_value = True
        mock_store.save_timetable_cache.return_value = True
        mock_run_once.return_value = {'success': True, 'data': source}

        response = client.post(
            '/api/search',
            json={
                'query': (
                    'I am from BSSE7A but I want to take Software Construction and Development theory '
                    'with BSSE5B and Software Quality Engineering and Testing with BSSE6A as well'
                ),
                'force_refresh': True,
            },
            headers={'X-User-Email': user['email']},
        )

        assert response.status_code == 200
        payload = response.get_json()
        assert [(item['semester_display'], item['course_code']) for item in payload['data']['items']] == [
            ('BS(SE)-7A', 'SEC 7001'),
            ('BS(SE)-7A', 'SEC 7002'),
            ('BS(SE)-5B', 'SEC 3604'),
            ('BS(SE)-6A', 'SEC 3608'),
        ]
        assert payload['data']['search']['query_plan']['selection_scope']['course_section_pairs'] == [
            {'section': 'BS(SE)-5B', 'kind': 'course', 'value': 'Software Construction and Development', 'class_types': ['theory']},
            {'section': 'BS(SE)-6A', 'kind': 'course', 'value': 'Software Quality Engineering and Testing', 'class_types': []},
        ]
        assert payload['data']['summary']['semester_breakdown'] == {
            'BS(SE)-7A': 2,
            'BS(SE)-5B': 1,
            'BS(SE)-6A': 1,
        }

    @patch('app.run_once')
    def test_saved_source_does_not_refresh_automatically_and_survives_failed_manual_refresh(self, mock_run_once, client, mock_store):
        user = {'id': 'stale-cache-user', 'email': 'stale@example.com'}
        item = {
            'schedule_day': 'Monday',
            'semester_display': 'BS(SE)-7A',
            'course': 'SEC 3603 Software Project Management (3,0)',
            'course_code': 'SEC 3603',
            'course_title': 'Software Project Management',
            'faculty': 'Ada Lovelace',
            'time': '02:00 PM - 03:30 PM',
        }
        source = {'items': [item], 'for_day': 'Entire Week', 'snapshot_version': 1, 'summary': {}}
        mock_store.get_or_create_user.return_value = user
        mock_store.get_user_settings.return_value = {}
        mock_store.get_search_source_cache.return_value = source
        mock_store.save_timetable_cache.return_value = True
        mock_run_once.return_value = {'success': False, 'error': 'Stored OAuth credentials could not be decrypted'}

        response = client.post(
            '/api/search',
            json={'query': 'BSSE7A classes on Monday'},
            headers={'X-User-Email': user['email']},
        )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload['cached'] is True
        assert payload['data']['items'] == [item]
        assert payload['data']['search']['source_stale'] is False
        mock_run_once.assert_not_called()

        refreshed = client.post(
            '/api/search',
            json={'query': 'BSSE7A classes on Monday', 'force_refresh': True},
            headers={'X-User-Email': user['email']},
        )
        assert refreshed.status_code == 400
        assert 'Gmail authorization' in refreshed.get_json()['error']

    def test_search_rejects_unbounded_query_input(self, client, mock_store, mock_user):
        mock_store.get_or_create_user.return_value = mock_user

        response = client.post(
            '/api/search',
            json={'query': 'x' * 501},
            headers={'X-User-Email': 'test@example.com'},
        )

        assert response.status_code == 400
        assert '500 characters' in response.get_json()['error']


class TestErrorHandling:
    def test_404_error(self, client):
        response = client.get('/api/nonexistent')
        assert response.status_code == 404
        assert b'Not Found' in response.data

    def test_405_error(self, client):
        response = client.delete('/api/health')
        assert response.status_code == 405
