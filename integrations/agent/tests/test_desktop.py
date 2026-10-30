"""Private driver boundary; does not substitute for native desktop E2E."""
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from sphinxsim_agent.desktop.managed import close_driver_connection, exchange_driver


class DesktopConnectionTests(unittest.TestCase):
    def test_observe_recovers_idle_connection_without_replaying_input(self):
        old = {"connection_id":"expired", "generation":"old"}
        new = {"connection_id":"fresh", "generation":"new"}
        error = HTTPError("http://127.0.0.1:6903",404,"expired",{},None)
        with patch("sphinxsim_agent.desktop.managed.driver_request", side_effect=[error,new,{"ok":True,"result":{"windows":[]}}]):
            connection, result = exchange_driver(old,{"name":"get_desktop_state"},observe=True)
        self.assertEqual(connection,new)
        self.assertEqual(result,{"ok":True,"result":{"windows":[]}})
        # Expiration during an input action remains an error; no replacement
        # connection is created and the action cannot run against a new capture.
        with patch("sphinxsim_agent.desktop.managed.driver_request", side_effect=[error,new]) as request:
            with self.assertRaises(HTTPError):
                exchange_driver(old,{"name":"click"},observe=False)
            self.assertEqual(request.call_count,1)

    def test_expired_driver_is_already_revoked_but_other_failures_block_handover(self):
        connection = {"connection_id": "expired", "generation": "old"}
        for status in (404, 409, 503):
            with self.subTest(status=status), patch("urllib.request.urlopen", side_effect=HTTPError("http://127.0.0.1:6903", status, "error", {}, None)):
                if status == 404:
                    close_driver_connection(connection)
                else:
                    with self.assertRaises(HTTPError):
                        close_driver_connection(connection)
