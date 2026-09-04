import json
import unittest
from datetime import datetime
from pathlib import Path

import matriz_prestamos_module as matrix


class MatrizPrestamosParserTests(unittest.TestCase):
    def test_reconstructs_reference_and_extracts_sample(self):
        rows = [
            {"name": "01_cabecera", "text": "MINISTERIO DE ECONOMAY FNANZAS No PEDIDO: 697"},
            {"name": "03_orden", "text": "MDEP-STN-2026-1819-O INT 59,472.87 COM 123,270.68 PRESTAMO BIRF-9595-EC"},
            {"name": "05_banco", "text": "Bco. del Benef.: CITIBANK N.A. CITIUS33"},
            {"name": "11_refer_swift", "text": "No. Refer Swift: TF-01-771260069"},
            {"name": "12_prestamo_ref", "text": "Prestamo: BIRF-9595-EC"},
            {"name": "14_oficio", "text": "MDEP-STN-2026-1819-O"},
            {"name": "17_fecha_valor", "text": "Fecha Valor: 01/09/2024"},
            {"name": "18_moneda", "text": "Moneda: USD"},
            {"name": "20_corresponsal", "text": "crTlBANK N.A. - NEW YORK"},
        ]
        result = matrix.parse_ocr_records(rows, datetime(2026, 8, 31), "bromo")
        entry = result["entry"]
        self.assertEqual(entry["refer_swift"], "TF-01-7712600697")
        self.assertEqual(entry["reference_loan"], "BIRF-9595-EC")
        self.assertEqual(entry["value_date"], "2026-09-01")
        self.assertEqual(entry["amount_usd"], 182743.55)
        self.assertEqual(entry["lender"], "BIRF")
        self.assertEqual(entry["borrower"], "MEF")
        self.assertEqual(entry["correspondent"], "CITIBANK")
        self.assertEqual(entry["beneficiary_bank"], "CITIBANK N.A.")
        self.assertEqual(entry["correspondent_confidence"], 100)
        self.assertEqual(result["confidence"], 100)

    def test_foreign_currency_uses_other_amount(self):
        rows = [
            {"name": "01_cabecera", "text": "MINISTERIO DE ECONOMIA Y FINANZAS PEDIDO: 12"},
            {"name": "03_orden", "text": "MDEP-STN-2026-1000-O INT 1,250.00 PRESTAMO BEI-1234-EC"},
            {"name": "11_refer_swift", "text": "GS-01-7712600012"},
            {"name": "12_prestamo_ref", "text": "BEI-1234-EC"},
            {"name": "17_fecha_valor", "text": "02/09/2026"},
            {"name": "18_moneda", "text": "EUR"},
        ]
        entry = matrix.parse_ocr_records(rows, datetime(2026, 8, 31), "spilaquinga")["entry"]
        self.assertIsNone(entry["amount_usd"])
        self.assertEqual(entry["amount_other"], 1250.0)
        self.assertEqual(entry["user"], "spilaquinga")

    def test_writer_and_ui_are_registered_in_source(self):
        base = Path(__file__).parent
        source = (base / "conciliacion_app.py").read_text(encoding="utf-8")
        self.assertIn('data-v="matrizprestamos"', source)
        self.assertIn('id="v-matrizprestamos"', source)
        self.assertIn("register_matriz_prestamos_routes(app, BASE_DIR)", source)
        self.assertIn("register_corresponsales_routes(app, BASE_DIR)", source)
        self.assertIn("https://www.swiftref.com/en/bicsearch", source)
        self.assertTrue((base / "matriz_prestamos_writer.mjs").is_file())
        self.assertTrue((base / "matriz_prestamos_ocr.ps1").is_file())


if __name__ == "__main__":
    unittest.main()
