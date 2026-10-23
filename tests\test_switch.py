"""
tests/test_switch.py
====================
Unit tests for Dead Man's Switch evaluation, threshold calculation,
and recipient seen-state filtering logic.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from scripts.check_switch import (
    SECONDS_PER_DAY,
    TRIGGER_THRESHOLD_DAYS,
    WARNING_THRESHOLD_DAYS,
    dispatch_notification,
    evaluate_inactivity,
    fetch_worker_last_heartbeat,
    fetch_worker_status,
    run_evaluation,
)


class TestThresholdEvaluation:
    def test_inactivity_healthy_threshold(self):
        now = 1700000000
        # 0.5 days ago (< 1.0 day)
        commit_ts = int(now - 0.5 * SECONDS_PER_DAY)
        status, days = evaluate_inactivity(commit_ts, now)
        assert status == "HEALTHY"
        assert pytest.approx(days, 0.01) == 0.5

    def test_inactivity_warning_threshold(self):
        now = 1700000000
        # 2.0 days ago (between 1.0 and 4.0 days -> warning / 3 rappels)
        commit_ts = int(now - 2.0 * SECONDS_PER_DAY)
        status, days = evaluate_inactivity(commit_ts, now)
        assert status == "WARNING"
        assert pytest.approx(days, 0.01) == 2.0

    def test_inactivity_triggered_threshold(self):
        now = 1700000000
        # 5.0 days ago (>= 4.0 days -> spread)
        commit_ts = int(now - 5.0 * SECONDS_PER_DAY)
        status, days = evaluate_inactivity(commit_ts, now)
        assert status == "TRIGGERED"
        assert pytest.approx(days, 0.01) == 5.0

    def test_boundary_values(self):
        now = 1700000000

        # Exactly 0.99 days -> HEALTHY
        commit_0_99 = int(now - 0.99 * SECONDS_PER_DAY)
        status, _ = evaluate_inactivity(commit_0_99, now)
        assert status == "HEALTHY"

        # Exactly 1.00 days -> WARNING
        commit_1_00 = int(now - 1.00 * SECONDS_PER_DAY)
        status, _ = evaluate_inactivity(commit_1_00, now)
        assert status == "WARNING"

        # Exactly 3.99 days -> WARNING
        commit_3_99 = int(now - 3.99 * SECONDS_PER_DAY)
        status, _ = evaluate_inactivity(commit_3_99, now)
        assert status == "WARNING"

        # Exactly 4.00 days -> TRIGGERED
        commit_4_00 = int(now - 4.00 * SECONDS_PER_DAY)
        status, _ = evaluate_inactivity(commit_4_00, now)
        assert status == "TRIGGERED"


class TestWorkerStatusFetching:
    @patch("requests.get")
    def test_fetch_worker_status_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "seen_recipients": {
                "alice": {"seen": True, "timestamp": 1700000000},
            },
        }
        mock_get.return_value = mock_response

        status_data = fetch_worker_status("https://worker.example.workers.dev", "test-token")
        assert "alice" in status_data
        assert status_data["alice"]["seen"] is True

    @patch("requests.get")
    def test_fetch_worker_status_failure_handles_gracefully(self, mock_get):
        mock_get.side_effect = Exception("Connection timeout")
        status_data = fetch_worker_status("https://worker.example.workers.dev", "token")
        assert status_data == {}

    @patch("requests.get")
    def test_fetch_worker_last_heartbeat_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "last_heartbeat": {"timestamp": 1700500000, "iso": "2023-11-20T00:00:00Z"},
        }
        mock_get.return_value = mock_response

        hb = fetch_worker_last_heartbeat("https://worker.example.workers.dev", "test-token")
        assert hb == 1700500000

    @patch("requests.get")
    def test_fetch_worker_last_heartbeat_failure(self, mock_get):
        mock_get.side_effect = Exception("Network down")
        hb = fetch_worker_last_heartbeat("https://worker.example.workers.dev", "token")
        assert hb is None


class TestSwitchEvaluationWorkflow:
    @pytest.fixture
    def sample_vault(self, tmp_path):
        vault_file = tmp_path / "vault.json"
        vault_content = {
            "version": "1.0",
            "recipients": [
                {
                    "id": "alice",
                    "name": "Alice Smith",
                    "email": "alice@example.com",
                    "salt": "a" * 32,
                    "iv": "b" * 24,
                    "ciphertext": "c" * 64,
                },
                {
                    "id": "bob",
                    "name": "Bob Jones",
                    "email": "bob@example.com",
                    "salt": "d" * 32,
                    "iv": "e" * 24,
                    "ciphertext": "f" * 64,
                },
            ],
        }
        vault_file.write_text(json.dumps(vault_content), encoding="utf-8")
        return str(vault_file)

    @patch("scripts.check_switch.dispatch_notification")
    def test_run_evaluation_healthy_silent_exit(self, mock_dispatch, sample_vault):
        now = 1700000000
        commit_ts = int(now - 2 * SECONDS_PER_DAY)

        exit_code = run_evaluation(
            vault_path=sample_vault,
            dry_run=True,
            override_commit_ts=commit_ts,
            override_now=now,
        )

        assert exit_code == 0
        mock_dispatch.assert_not_called()

    @patch("scripts.check_switch.dispatch_notification")
    def test_run_evaluation_warning_notifies_owner_only(self, mock_dispatch, sample_vault, monkeypatch):
        monkeypatch.setenv("OWNER_EMAIL", "owner@example.com")
        now = 1700000000
        commit_ts = int(now - 2 * SECONDS_PER_DAY)

        exit_code = run_evaluation(
            vault_path=sample_vault,
            dry_run=True,
            override_commit_ts=commit_ts,
            override_now=now,
        )

        assert exit_code == 0
        assert mock_dispatch.call_count == 1
        args, kwargs = mock_dispatch.call_args
        to_email = args[0]
        subject = args[1]
        assert to_email == "owner@example.com"
        assert "Dead Man's Switch" in subject

    @patch("scripts.check_switch.fetch_worker_status")
    @patch("scripts.check_switch.dispatch_notification")
    def test_run_evaluation_triggered_notifies_unseen_recipients(
        self, mock_dispatch, mock_worker_status, sample_vault
    ):
        now = 1700000000
        commit_ts = int(now - 35 * SECONDS_PER_DAY)
        mock_worker_status.return_value = {}  # No one has seen yet

        exit_code = run_evaluation(
            vault_path=sample_vault,
            dry_run=True,
            override_commit_ts=commit_ts,
            override_now=now,
        )

        assert exit_code == 0
        assert mock_dispatch.call_count == 2
        notified_emails = [call[0][0] for call in mock_dispatch.call_args_list]
        assert "alice@example.com" in notified_emails
        assert "bob@example.com" in notified_emails

    @patch("scripts.check_switch.fetch_worker_status")
    @patch("scripts.check_switch.dispatch_notification")
    def test_run_evaluation_triggered_filters_already_seen_recipients(
        self, mock_dispatch, mock_worker_status, sample_vault
    ):
        now = 1700000000
        commit_ts = int(now - 35 * SECONDS_PER_DAY)

        # Alice has already seen and acknowledged her payload in Cloudflare KV
        mock_worker_status.return_value = {
            "alice": {"seen": True, "timestamp": 1699990000, "iso": "2023-11-14T20:00:00Z"},
        }

        exit_code = run_evaluation(
            vault_path=sample_vault,
            dry_run=True,
            override_commit_ts=commit_ts,
            override_now=now,
        )

        assert exit_code == 0
        # Only Bob should receive notification!
        assert mock_dispatch.call_count == 1
        to_email = mock_dispatch.call_args[0][0]
        assert to_email == "bob@example.com"

    @patch("scripts.check_switch.fetch_worker_status")
    @patch("scripts.check_switch.dispatch_notification")
    def test_run_evaluation_triggered_all_acknowledged_no_emails_sent(
        self, mock_dispatch, mock_worker_status, sample_vault
    ):
        now = 1700000000
        commit_ts = int(now - 35 * SECONDS_PER_DAY)

        # Both Alice and Bob have acknowledged
        mock_worker_status.return_value = {
            "alice": {"seen": True, "timestamp": 1699990000},
            "bob": {"seen": True, "timestamp": 1699995000},
        }

        exit_code = run_evaluation(
            vault_path=sample_vault,
            dry_run=True,
            override_commit_ts=commit_ts,
            override_now=now,
        )

        assert exit_code == 0
        # Zero emails sent because everyone already acknowledged!
        mock_dispatch.assert_not_called()


class TestEmailNotificationBackends:
    @patch("requests.post")
    def test_resend_backend(self, mock_post, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test_123")
        monkeypatch.delenv("DMS_DRY_RUN", raising=False)
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_post.return_value = mock_res

        success = dispatch_notification("test@example.com", "Subject", "Body", dry_run=False)
        assert success is True
        assert mock_post.called
        assert "api.resend.com" in mock_post.call_args[0][0]

    @patch("requests.post")
    def test_sendgrid_backend(self, mock_post, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        monkeypatch.setenv("SENDGRID_API_KEY", "SG.test_123")
        monkeypatch.delenv("DMS_DRY_RUN", raising=False)
        mock_res = MagicMock()
        mock_res.status_code = 202
        mock_post.return_value = mock_res

        success = dispatch_notification("test@example.com", "Subject", "Body", dry_run=False)
        assert success is True
        assert mock_post.called
        assert "api.sendgrid.com" in mock_post.call_args[0][0]

    def test_dry_run_flag(self):
        success = dispatch_notification("test@example.com", "Subject", "Body", dry_run=True)
        assert success is True
