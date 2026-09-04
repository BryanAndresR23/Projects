import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, jsonify

from auth_module import register_auth
from control_operativo_module import build_control_status


class SecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        register_auth(self.app, self.temp.name)

        @self.app.post("/api/example/run")
        def run_action():
            return jsonify({"ok": True})

        @self.app.post("/api/example/config")
        def change_config():
            return jsonify({"ok": True})

        @self.app.post("/api/aplicar_pago_directo")
        def approve_adjustment():
            return jsonify({"ok": True})

        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def login(self, username="bromo"):
        response = self.client.post(
            "/login", data={"username": username, "password": "password.1"}
        )
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as user_session:
            return user_session["csrf_token"]

    def test_mutating_api_requires_csrf_and_security_headers(self):
        token = self.login()
        rejected = self.client.post("/api/example/run")
        accepted = self.client.post(
            "/api/example/run", headers={"X-CSRF-Token": token}
        )
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.headers["X-Content-Type-Options"], "nosniff")
        self.assertTrue(accepted.headers["X-Request-ID"])

    def test_configuration_requires_administrator(self):
        token = self.login("spilaquinga")
        response = self.client.post(
            "/api/example/config", headers={"X-CSRF-Token": token}
        )
        self.assertEqual(response.status_code, 403)

    def test_adjustment_requires_supervisor(self):
        token = self.login("spilaquinga")
        response = self.client.post(
            "/api/aplicar_pago_directo", headers={"X-CSRF-Token": token}
        )
        self.assertEqual(response.status_code, 403)

    def test_mutations_are_audited(self):
        token = self.login()
        self.client.post("/api/example/run", headers={"X-CSRF-Token": token})
        database = Path(self.temp.name, "gidep_control.db")
        self.assertTrue(database.is_file())
        events = self.client.get("/api/sistema/auditoria").get_json()["events"]
        self.assertTrue(any(item["ruta"] == "/api/example/run" for item in events))


class ControlOperativoTests(unittest.TestCase):
    def test_builds_a_single_attention_queue(self):
        agenda_payload = {
            "scan": {
                "scanned_at": "2026-09-03T08:00:00-05:00",
                "summary": {"today_pending": 2, "overdue": 1},
            },
            "monitor": {},
        }
        quipux_payload = {"summary": {"review": 3, "incomplete": 1}}
        contracts_payload = {"summary": {"review": 0}}
        vouchers_payload = {"summary": {"review": 1, "ack_pending": 2}}
        signatures_payload = {
            "summary": {"pending_signatures": 1},
            "generated_at": "2026-09-03T08:00:00-05:00",
        }
        with (
            patch("control_operativo_module.agenda.AGENDA.status", return_value=agenda_payload),
            patch("control_operativo_module.quipux.status_payload", return_value=quipux_payload),
            patch("control_operativo_module.contracts.status_payload", return_value=contracts_payload),
            patch("control_operativo_module.vouchers.state_response", return_value=vouchers_payload),
            patch("control_operativo_module.signatures._status_monitor") as monitor,
        ):
            monitor.snapshot.return_value = signatures_payload
            payload = build_control_status(self.temp_dir())
        cards = {item["id"]: item["value"] for item in payload["cards"]}
        self.assertEqual(cards["today"], 2)
        self.assertEqual(cards["acks"], 2)
        self.assertGreaterEqual(len(payload["issues"]), 6)

    @staticmethod
    def temp_dir():
        return Path(tempfile.gettempdir())


if __name__ == "__main__":
    unittest.main()
