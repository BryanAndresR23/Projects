import tempfile
import unittest

from flask import Flask, jsonify

from auth_module import register_auth


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        register_auth(self.app, self.temp.name)

        @self.app.get("/")
        def protected_home():
            return "OK"

        @self.app.get("/api/private")
        def private_api():
            return jsonify({"ok": True})

        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_requires_session(self):
        page = self.client.get("/")
        api = self.client.get("/api/private")
        self.assertEqual(page.status_code, 302)
        self.assertIn("/login", page.headers["Location"])
        self.assertEqual(api.status_code, 401)

    def test_configured_users_can_login(self):
        for username in ("bromo", "spilaquinga", "dvaldivieso"):
            with self.subTest(username=username):
                client = self.app.test_client()
                response = client.post(
                    "/login",
                    data={"username": username, "password": "password.1", "next": "/"},
                    follow_redirects=True,
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, b"OK")

    def test_rejects_invalid_password(self):
        response = self.client.post(
            "/login",
            data={"username": "bromo", "password": "incorrecta"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("incorrectos".encode("utf-8"), response.data)


if __name__ == "__main__":
    unittest.main()

