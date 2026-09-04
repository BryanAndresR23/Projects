from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import mensajes_firmados_module as module


def make_flow(flow_id: str, label: str, source: Path, strategy: str):
    return {
        "id": flow_id,
        "label": label,
        "source": source,
        "strategy": strategy,
        "description": "Flujo de prueba",
    }


class FirmaECTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        module.invalidate_archive_cache()

    def tearDown(self):
        self.temp.cleanup()

    def write_pdf(self, path: Path, content: bytes = b"%PDF-test") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_daily_target_uses_spanish_month_and_day(self):
        target = module.daily_target_root(date(2026, 8, 14), self.root)
        self.assertEqual(target, self.root / "2026" / "08. AGOSTO" / "14")
        self.assertEqual(module.AUTOMATIC_REFRESH_SECONDS, 3)

    def test_ui_uses_realtime_three_flow_monitor(self):
        source = (Path(__file__).parent / "conciliacion_app.py").read_text(encoding="utf-8")
        self.assertIn("Monitor en tiempo real · cada 3 segundos", source)
        self.assertIn("FIRM.timer=setInterval(firmLoad,3000)", source)
        self.assertIn('window.addEventListener("focus",firmLoad)', source)
        self.assertIn("Listos para copiar", source)
        self.assertIn("conserva en el origen las versiones de una y dos firmas", source)

    def test_monitor_returns_warming_snapshot_without_waiting_for_network(self):
        monitor = module.SignatureStatusMonitor()
        snapshot = monitor.snapshot()
        self.assertTrue(snapshot["monitor_warming"])
        self.assertEqual(snapshot["automatic_refresh_seconds"], 3)
        self.assertEqual(len(snapshot["flows"]), 3)

    def test_payment_flow_detects_pending_and_double_signed(self):
        source = self.root / "BRYAN"
        folder = source / "2026-771-1008"
        self.write_pdf(folder / "TF-01-7612600668-signed.pdf")
        self.write_pdf(folder / "GS-01-7712600999-signed-signed.pdf")
        target = self.root / "archive" / "2026" / "08. AGOSTO" / "22"
        target.mkdir(parents=True)
        flow = make_flow("deuda_externa", "Firma Deuda Externa", source, "payment_folders")

        alerts = module.discover_signature_alerts(flow, target)
        messages = module.discover_messages(flow, target)

        self.assertEqual([item["operation_reference"] for item in alerts], ["TF-01-7612600668"])
        self.assertEqual(alerts[0]["signature_status"], "second_signature")
        self.assertEqual([item["operation_reference"] for item in messages], ["GS-01-7712600999"])
        self.assertEqual(messages[0]["voucher_number"], "771-1008")

    def test_dated_flow_reads_recent_folders_and_real_filename_prefix(self):
        source = self.root / "MENSAJES DIGITALIZADOS"
        today = module.local_today()
        recent = source / today.isoformat()
        old = source / (today - timedelta(days=30)).isoformat()
        self.write_pdf(recent / "MENSAJES SWIFT GS-01-7502607723-signed.pdf")
        self.write_pdf(recent / "MENSAJES SWIFT TF-01-7502607999-signed-signed.pdf")
        self.write_pdf(old / "MENSAJES SWIFT TF-01-7502607000-signed.pdf")
        target = self.root / "target"
        target.mkdir()
        flow = make_flow("automaticos", "Firmas Automáticos", source, "dated_folders")

        alerts = module.discover_signature_alerts(flow, target)
        messages = module.discover_messages(flow, target)

        self.assertEqual([item["operation_reference"] for item in alerts], ["GS-01-7502607723"])
        self.assertEqual([item["operation_reference"] for item in messages], ["TF-01-7502607999"])
        self.assertEqual(alerts[0]["voucher_number"], today.isoformat())

    def test_archived_double_signature_suppresses_pending_alert(self):
        source = self.root / "BANCOS"
        today_folder = source / module.local_today().isoformat()
        self.write_pdf(today_folder / "MENSAJES SWIFT TF-01-7502607918-signed.pdf")
        target = self.root / "target"
        self.write_pdf(target / "MENSAJES SWIFT TF-01-7502607918-signed-signed.pdf")
        flow = make_flow("bancos", "Firma Bancos", source, "dated_folders")

        self.assertEqual(module.discover_signature_alerts(flow, target), [])

    def test_copy_is_flow_scoped_audited_and_preserves_both_signatures(self):
        source = self.root / "BANCOS"
        daily_source = source / module.local_today().isoformat()
        single_signature = self.write_pdf(
            daily_source / "MENSAJES SWIFT TF-01-7502608123-signed.pdf",
            b"signed once",
        )
        unrelated_single = self.write_pdf(
            daily_source / "MENSAJES SWIFT TF-01-7502608999-signed.pdf",
            b"another operation",
        )
        message = self.write_pdf(
            daily_source / "MENSAJES SWIFT TF-01-7502608123-signed-signed.pdf",
            b"signed twice",
        )
        target = self.root / "target"
        target.mkdir()
        audit = self.root / "audit" / "movimientos.log"
        flow = make_flow("bancos", "Firma Bancos", source, "dated_folders")
        flow_map = {"bancos": flow}
        item = module.discover_messages(flow, target)[0]

        results = module.move_messages(
            "bancos", [item["message_id"]], audit, target, flow_map
        )

        self.assertTrue(message.exists())
        self.assertTrue(single_signature.exists())
        self.assertTrue(unrelated_single.exists())
        self.assertTrue((target / message.name).is_file())
        self.assertEqual(results[0]["result"], "copied")
        self.assertTrue(results[0]["transfer_verified"])
        self.assertTrue(results[0]["source_preserved"])
        self.assertTrue(results[0]["single_signature_preserved"])
        self.assertFalse(results[0]["single_signature_deleted"])
        record = json.loads(audit.read_text(encoding="utf-8").strip())
        self.assertEqual(record["flow_label"], "Firma Bancos")
        self.assertEqual(record["action"], "copied")
        self.assertTrue(record["source_preserved"])
        self.assertTrue(record["single_signature_preserved"])
        self.assertFalse(record["single_signature_deleted"])

    def test_single_signature_is_preserved_when_double_signature_has_conflict(self):
        source = self.root / "BANCOS"
        daily_source = source / module.local_today().isoformat()
        single_signature = self.write_pdf(
            daily_source / "TF-01-7502608222-signed.pdf", b"signed once"
        )
        double_signature = self.write_pdf(
            daily_source / "TF-01-7502608222-signed-signed.pdf", b"signed twice"
        )
        target = self.root / "target"
        self.write_pdf(target / double_signature.name, b"different target")
        audit = self.root / "audit" / "movimientos.log"
        flow = make_flow("bancos", "Firma Bancos", source, "dated_folders")
        item = module.discover_messages(flow, target)[0]

        results = module.move_messages(
            "bancos", [item["message_id"]], audit, target, {"bancos": flow}
        )

        self.assertEqual(results[0]["result"], "skipped")
        self.assertTrue(single_signature.exists())
        self.assertTrue(double_signature.exists())
        self.assertFalse(audit.exists())


if __name__ == "__main__":
    unittest.main()
