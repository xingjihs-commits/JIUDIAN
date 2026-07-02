"""heartbeat_once — 厂家控制台手动云端同步入口。"""
from __future__ import annotations

from unittest.mock import patch

from heartbeat_service import heartbeat_once


def test_heartbeat_once_no_worker_url():
    with patch("heartbeat_service.db") as mdb:
        mdb.get_config.side_effect = lambda k: {
            "cloud_worker_url": "",
            "hotel_id": "H1",
            "cloud_enabled": "1",
        }.get(k, "")
        assert "Worker" in heartbeat_once()


def test_heartbeat_once_success():
    with patch("heartbeat_service.db") as mdb:
        mdb.get_config.side_effect = lambda k: {
            "cloud_worker_url": "https://example.workers.dev",
            "hotel_id": "H1",
            "cloud_enabled": "1",
        }.get(k, "")
        with patch("heartbeat_service.HeartbeatService._send_heartbeat", return_value=True):
            assert "成功" in heartbeat_once()
