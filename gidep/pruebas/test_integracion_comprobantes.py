import os
import logging
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from flask import Flask

os.environ["COMPROBANTES_DISABLE_AUTOSTART"] = "1"

import comprobantes_contables_engine as engine
import comprobantes_contables_module as module


class ComprobantesIntegrationTests(unittest.TestCase):
    def setUp(self):
        module._state.update(
            {
                "analysis_id": None,
                "generated_at": None,
                "payments": {},
                "analysis_origin": None,
                "auto_status": "INICIANDO",
                "auto_error": None,
            }
        )

    def test_config_migrates_bryan_and_keeps_steven(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base / module.CONFIG_FILENAME).write_text(
                '{"source_dir":"Z:\\\\ORIGEN\\\\Bryan",'
                '"acks_root":"Z:\\\\ACK"}',
                encoding="utf-8",
            )
            config = module.load_config(base)
            self.assertEqual(config["bryan_source"], r"Z:\ORIGEN\Bryan")
            self.assertEqual(config["steven_source"], str(engine.DEFAULT_STEVEN_SOURCE))
            self.assertEqual(config["ack_base"], r"Z:\ACK")

    def test_ui_opens_with_daily_pending_view_and_keeps_history_filter(self):
        source = (Path(__file__).parent / "conciliacion_app.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("Control diario de archivo", source)
        self.assertIn('data-comp-view="pending"', source)
        self.assertIn('id="compDateMode"', source)
        self.assertIn('<option value="today">Hoy</option>', source)
        self.assertIn('dateMode:"today"', source)
        self.assertIn('view:"pending"', source)
        self.assertIn("function compFilteredPayments()", source)
        self.assertIn("Mostrando ${payments.length} de ${COMP2.payments.length}", source)

    def test_routes_publish_both_sources_and_archive_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            app = Flask(__name__)
            module.register_comprobantes_contables_routes(app, base)
            payment = engine.Payment(
                marker=base / "TF-01-7712600001-signed-signed.pdf",
                source_folder=base,
                operation_reference="TF-01-7712600001",
                voucher_number="771-1",
                responsible="STEVEN",
            )
            payment.status = "LISTO"
            payment.match_score = 100

            with app.test_client() as client, patch.object(
                module, "analyze", return_value=[payment]
            ) as analyze_mock:
                response = client.post(
                    "/api/comprobantes_contables/analyze",
                    json={
                        "bryan_source": str(base / "BRYAN"),
                        "steven_source": str(base / "STEVEN"),
                        "ack_base": str(base / "ACK"),
                        "destination": str(base / "DESTINO"),
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["payments"][0]["responsible"], "STEVEN")
                passed_sources = analyze_mock.call_args.args[0]
                self.assertEqual([name for name, _ in passed_sources], ["BRYAN", "STEVEN"])

                payment_id = payload["payments"][0]["payment_id"]

                def fake_execute(item, allow_probable=False):
                    item.status = "ARCHIVADO"

                with patch.object(module.engine, "classify"), patch.object(
                    module.engine, "execute", side_effect=fake_execute
                ):
                    archived = client.post(
                        "/api/comprobantes_contables/archive",
                        json={
                            "analysis_id": payload["analysis_id"],
                            "references": [payment_id],
                            "confirmation": "ARCHIVAR",
                        },
                    )
                self.assertEqual(archived.status_code, 200)
                self.assertEqual(
                    archived.get_json()["processed"][0]["status"], "ARCHIVADO"
                )
            if module._logger is not None:
                for handler in list(module._logger.handlers):
                    handler.close()
                    module._logger.removeHandler(handler)
                module._logger = None

    def test_ack_queue_survives_missing_marker_and_completes_when_ack_arrives(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "BRYAN" / "2026-771-1057"
            destination = base / "DESTINO" / "BIRF-9595"
            source.mkdir(parents=True)
            destination.mkdir(parents=True)
            accounting_source = source / "771-1057-signed.pdf"
            accounting_target = destination / "Comprobante Contable No. 771-1057 01Sep2026.pdf"
            ack_source = base / "ACK" / "TF-01-7712600697.pdf"
            ack_target = destination / ack_source.name
            accounting_source.write_bytes(b"comprobante")
            accounting_target.write_bytes(b"comprobante")
            payment = engine.Payment(
                marker=source / "TF-01-7712600697-signed-signed.pdf",
                source_folder=source,
                operation_reference="TF-01-7712600697",
                voucher_number="771-1057",
                responsible="BRYAN",
                accounting_source=accounting_source,
                ack_source=ack_source,
                value_date=datetime(2026, 9, 1),
                loan_reference="BIRF-9595",
                destination=destination,
                accounting_target=accounting_target,
                ack_target=ack_target,
                match_method="Referencia de prestamo exacta",
                match_score=100,
                status="ACK PENDIENTE",
            )

            module.register_pending_ack_payments(base, [payment])
            logger = logging.getLogger("test.ack.queue")
            pending = module.process_pending_ack_queue(base, logger)
            self.assertEqual(pending[0].status, "ACK PENDIENTE")
            self.assertFalse(payment.marker.exists())
            self.assertTrue(accounting_source.exists())

            ack_source.parent.mkdir(parents=True)
            ack_source.write_bytes(b"acuse de recibo")
            completed = module.process_pending_ack_queue(base, logger)

            self.assertEqual(completed[0].status, "ARCHIVADO")
            self.assertEqual(ack_target.read_bytes(), b"acuse de recibo")
            self.assertTrue(ack_source.exists())
            self.assertTrue(accounting_source.exists())
            restored = module.load_ack_queue(base)
            self.assertEqual(
                restored["BRYAN|771-1057|TF-01-7712600697"]["status"],
                "ARCHIVADO",
            )


if __name__ == "__main__":
    unittest.main()
