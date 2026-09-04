import tempfile
import unittest
from pathlib import Path

from flask import Flask

from activaciones_cuentas_module import register_activaciones_cuentas_routes
from respuestas_correos_module import register_respuestas_correos_routes


class NuevosModulosTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        register_activaciones_cuentas_routes(self.app, self.temp_dir.name)
        register_respuestas_correos_routes(self.app, self.temp_dir.name)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_validador_swift_esta_publicado(self):
        status = self.client.get("/api/activaciones-cuentas/status")
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.get_json()["ok"])

    def test_aprende_y_sugiere_respuesta_local(self):
        learned = self.client.post(
            "/api/respuestas-correos/learn",
            json={
                "category": "Pagos",
                "subject": "Confirmación de pago BID",
                "incoming": "Solicitamos confirmar el pago del préstamo BID.",
                "response": "Confirmamos que el pago fue procesado correctamente.",
                "notes": "Usar cuando el comprobante ya esté disponible.",
            },
        )
        self.assertEqual(learned.status_code, 200)
        self.assertTrue(learned.get_json()["ok"])

        suggested = self.client.post(
            "/api/respuestas-correos/suggest",
            json={
                "category": "Pagos",
                "subject": "Pago BID",
                "incoming": "Por favor confirmar el pago del préstamo BID.",
            },
        )
        payload = suggested.get_json()
        self.assertEqual(suggested.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("pago fue procesado", payload["suggestion"])
        self.assertGreater(payload["confidence"], 0)

        examples = self.client.get("/api/respuestas-correos/examples").get_json()
        self.assertEqual(examples["total"], 1)
        self.assertTrue(Path(self.temp_dir.name, "respuestas_correos.db").is_file())

    def test_interfaz_permite_iniciar_un_correo_en_blanco(self):
        source = Path(__file__).with_name("conciliacion_app.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="mailReset"', source)
        self.assertIn("function mailResetAnalysis()", source)
        self.assertIn('mailEl("mailSuggestedResponse").value=""', source)


if __name__ == "__main__":
    unittest.main()
