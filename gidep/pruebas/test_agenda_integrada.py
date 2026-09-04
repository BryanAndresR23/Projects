from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ["AGENDA_DISABLE_AUTOSTART"] = "1"
os.environ["COMPROBANTES_DISABLE_AUTOSTART"] = "1"
os.environ["FIRM_DISABLE_AUTOSTART"] = "1"

import agenda_pagos_module as agenda


def fake_pdf_text(path: Path, use_ocr: bool = True):
    del use_ocr
    return path.read_text(encoding="utf-8"), False


def payment(**changes):
    values = {
        "payment_id": "BID|pago|1",
        "responsible": "BID",
        "voucher_number": "—",
        "source_folder": "C:/pago",
        "source_document": "C:/pago/Oficio.pdf",
        "document_name": "Oficio.pdf",
        "office_reference": "MDEP-STN-2026-1700-O",
        "issuer": "MEF",
        "loan_reference": "BID-1234",
        "loan_folder": "BID-1234",
        "value_date": "2026-08-22",
        "date_type": "Fecha valor",
        "due_date": "2026-08-23",
        "amount_value": "1,250.50",
        "currency": "USD",
        "status": "today",
        "timing": "today",
        "days_until": 0,
        "completed": False,
        "accounting_ready": False,
        "operation_reference": None,
        "confidence": 100,
        "ocr_used": False,
        "note": "Debe procesarse hoy.",
    }
    values.update(changes)
    return agenda.ScheduledPayment(**values)


class AgendaExtractionTests(unittest.TestCase):
    def setUp(self):
        agenda._candidate_cache = {}
        agenda._cache_dirty = False
        agenda.supporting_bank_evidence.cache_clear()

    def test_debit_date_has_priority(self):
        extracted, label = agenda.extract_value_date(
            "FECHA VALOR 23/08/2026. FECHA DE DEBITO 22/08/2026."
        )
        self.assertEqual(extracted, date(2026, 8, 22))
        self.assertEqual(label, "Fecha de débito")

    def test_identify_issuer_accepts_missing_office(self):
        self.assertEqual(agenda.identify_issuer("", None), "Institución")

    def test_stale_snapshot_is_reclassified_before_full_network_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "agenda_snapshot.json"
            snapshot.write_text(
                '{"items":[{"source_folder":"Z:/BID/BID-2487 - República Ecuador",'
                '"responsible":"BID","issuer":"Institución",'
                '"office_reference":"MDEP-STN-2026-1825-O",'
                '"loan_reference":"BID-2487","currency":"USD",'
                '"beneficiary_bank":"THE BANK OF NEW YORK MELLON",'
                '"correspondent":"JPMORGAN"}]}',
                encoding="utf-8",
            )
            with patch.object(agenda, "SNAPSHOT_FILE", snapshot):
                loaded = agenda.load_agenda_snapshot()

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["items"][0]["issuer"], "MDEP")
        self.assertEqual(loaded["items"][0]["correspondent"], "FEDERAL")
        self.assertEqual(
            loaded["items"][0]["correspondent_full"],
            "FEDERAL RESERVE BANK OF NEW YORK",
        )

    def test_extracts_daily_summary_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "BID-2761 - 20295000 - República Ecuador"
            folder.mkdir()
            office = folder / "Oficio MDEP-STN-2026-1679-O.pdf"
            office.write_text(
                "Oficio No. MDEP-STN-2026-1679-O\n"
                "Prestamo BID-2761-OC-EC\n"
                "Fecha de vencimiento: 23 de agosto de 2026\n"
                "Fecha valor: 22 de agosto de 2026\n"
                "Monto total USD 238,647.81",
                encoding="utf-8",
            )
            with patch.object(agenda, "searchable_text", side_effect=fake_pdf_text):
                item = agenda.classify_folder(
                    "BID", folder, today=date(2026, 8, 22), use_ocr=False
                )
        self.assertIsNotNone(item)
        self.assertEqual(item.loan_reference, "BID-2761-OC-EC")
        self.assertEqual(item.value_date, "2026-08-22")
        self.assertEqual(item.amount_value, "238,647.81")
        self.assertEqual(item.currency, "USD")
        self.assertEqual(item.status, "today")

    def test_reads_account_statement_to_identify_correspondent(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "BIRF-9595-EC - República Ecuador"
            folder.mkdir()
            (folder / "Oficio MDEP-STN-2026-1819-O.pdf").write_text(
                "Oficio No. MDEP-STN-2026-1819-O\n"
                "MINISTERIO DE ECONOMIA Y FINANZAS\n"
                "Prestamo BIRF-9595-EC\nFecha valor: 01/09/2026\nMonto USD 182,743.55",
                encoding="utf-8",
            )
            (folder / "Estado de Cuenta Citibank.pdf").write_text(
                "BANCO DEL BENEFICIARIO: CITIBANK N.A. CITIUS33",
                encoding="utf-8",
            )
            with patch.object(agenda, "searchable_text", side_effect=fake_pdf_text):
                item = agenda.classify_folder(
                    "BIRF", folder, today=date(2026, 9, 1), use_ocr=False
                )
        self.assertIsNotNone(item)
        self.assertEqual(item.beneficiary_bank, "CITIBANK N.A.")
        self.assertEqual(item.correspondent, "CITIBANK")
        self.assertEqual(item.correspondent_confidence, 100)
        self.assertEqual(item.account_statements_reviewed, 1)

    def test_bid_2487_republic_of_ecuador_uses_federal_reserve(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = (
                Path(temporary)
                / "BID-2487-OC-EC - 20287000 - República Ecuador"
            )
            folder.mkdir()
            (folder / "Oficio No. MDEP-STN-2026-1825-O.pdf").write_text(
                "Oficio No. MDEP-STN-2026-1825-O\n"
                "Prestamo BID-2487-OC-EC\n"
                "Fecha valor: 03/09/2026\n"
                "Monto total USD 1,990,048.14\n"
                "Banco del beneficiario: THE BANK OF NEW YORK MELLON",
                encoding="utf-8",
            )
            with patch.object(agenda, "searchable_text", side_effect=fake_pdf_text):
                item = agenda.classify_folder(
                    "BID", folder, today=date(2026, 9, 3), use_ocr=False
                )

        self.assertIsNotNone(item)
        self.assertEqual(item.issuer, "MDEP")
        self.assertEqual(item.correspondent, "FEDERAL")
        self.assertGreaterEqual(item.correspondent_confidence, 96)
        self.assertIn(
            item.correspondent_method,
            {"República del Ecuador + guía", "Préstamo exacto en matriz"},
        )

    def test_totals_remain_separated_by_currency(self):
        items = [
            payment(amount_value="1,250.50", currency="USD"),
            payment(payment_id="2", amount_value="750,50", currency="USD"),
            payment(payment_id="3", amount_value="80.00", currency="EUR"),
            payment(payment_id="4", amount_value="999", currency="USD", days_until=1),
        ]
        self.assertEqual(
            agenda.totals_by_currency(items, today_only=True, pending_only=True),
            {"EUR": 80.0, "USD": 2001.0},
        )

    def test_duplicate_office_prefers_completed_record(self):
        original = payment(loan_folder="BID-1234")
        duplicate = payment(
            payment_id="copy",
            loan_folder="BID-1234 (2)",
            completed=True,
            status="paid",
        )
        result = agenda.deduplicate_payments([original, duplicate])
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].completed)


class AgendaRouteTests(unittest.TestCase):
    def test_agenda_is_integrated_as_independent_section(self):
        import conciliacion_app

        conciliacion_app.app.testing = True
        client = conciliacion_app.app.test_client()
        with client.session_transaction() as user_session:
            user_session["username"] = "bromo"
            user_session["display_name"] = "Bromo"
            user_session["role"] = "Administrador"
        with patch.object(
            agenda.AGENDA,
            "status",
            return_value={"ok": True, "automatic": True, "monitor": {}, "scan": None},
        ):
            page = client.get("/")
            status = client.get("/api/agenda-pagos/status")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Agenda Diaria de Pagos", page.data)
        self.assertIn("Gestor Integral de Deuda Externa Pública - Banco Central del Ecuador".encode("utf-8"), page.data)
        self.assertIn(b'id="concFlowTabs"', page.data)
        self.assertIn(b'data-conc-view="condonados"', page.data)
        self.assertNotIn(b'id="navConciliacionToggle"', page.data)
        self.assertIn(b'data-v="activaciones"', page.data)
        self.assertIn(b'data-v="respuestas"', page.data)
        self.assertIn(b"https://www.swiftref.com/en/bicsearch", page.data)
        self.assertIn("Corresponsal".encode("utf-8"), page.data)
        self.assertNotIn(b"cargarUltimoConciliado(true);", page.data)
        self.assertIn(b"Sin conciliaci", page.data)
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.get_json()["automatic"])


if __name__ == "__main__":
    unittest.main()
