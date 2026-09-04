import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import archivo_quipux_module as module


class ArchivoQuipuxTests(unittest.TestCase):
    def test_clasifica_los_tres_documentos_institucionales(self):
        self.assertEqual(
            module.classify_document(
                Path("of-emapag-ep-gg-2026-0864.pdf"),
                "Oficio Nro. EMAPAG-EP-GG-2026-0864",
            ),
            "OFICIO",
        )
        self.assertEqual(
            module.classify_document(
                Path("orden_de_pago_bid_2201_oc-ec.pdf"), "Payment request"
            ),
            "ESTADO CUENTA",
        )
        self.assertEqual(
            module.classify_document(
                Path("formulario_giro_al_exterior_00894-signed.pdf"), ""
            ),
            "FORMULARIO",
        )

    def test_extrae_referencias_fecha_moneda_y_monto(self):
        text = (
            "Oficio Nro. MDEP-STN-2026-1724-O. Préstamo BID-2201-OC-EC "
            "SIGADE: 20280000. Fecha valor: 19/08/2026. USD 125,400.75"
        )
        metadata = module.extract_metadata(Path("oficio.pdf"), text, False)
        self.assertIn("MDEP-STN-2026-1724-O", metadata["office_references"])
        self.assertIn("BID-2201-OC-EC", metadata["loan_references"])
        self.assertIn("20280000", metadata["sigade_numbers"])
        self.assertEqual(metadata["value_date"].strftime("%d/%m/%Y"), "19/08/2026")
        self.assertEqual(metadata["currency"], "USD")
        self.assertIn("125400.75", metadata["amounts"])

    def test_emapag_usa_fecha_de_pago_y_normaliza_prestamo_bei(self):
        text = (
            "Referencia: Deuda Externa P bl ca Préstamo FI 84689 - BEI. "
            "Pago de Intereses y Capital correspondiente al 27/02/2026 al "
            "27/08/2026. Valor que deberá ser pagado el día 25.08.2026. "
            "USD 602,370.00"
        )
        metadata = module.extract_metadata(
            Path("of-emapag-ep-gg-2026-0864.pdf"), text, True
        )
        self.assertIn("BEI-84689", metadata["loan_references"])
        self.assertNotIn("BEI-PAGO", metadata["loan_references"])
        self.assertIn(
            "EMAPAG-EP-GG-2026-0864", metadata["office_references"]
        )
        self.assertEqual(metadata["value_date"].strftime("%d/%m/%Y"), "25/08/2026")
        self.assertTrue(metadata["public_debt_payment"])

    def test_memorando_administrativo_no_entra_al_flujo_de_deuda(self):
        text = (
            "Memorando Nro. BCE-GAF-2026-0897-M. Asunto: capacitación "
            "institucional y actualización de procedimientos internos."
        )
        metadata = module.extract_metadata(
            Path("BCE-GAF-2026-0897-M.pdf"), text, False
        )
        self.assertEqual(metadata["document_type"], "MEMORANDO")
        self.assertFalse(metadata["public_debt_payment"])

    def test_match_exacto_por_sigade(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = root / "BID-2201-OC-EC - 20280000 - República Ecuador"
            other = root / "BID-2761-OC-EC - 20295000 - República Ecuador"
            expected.mkdir()
            other.mkdir()
            metadata = {
                "loan_references": [],
                "sigade_numbers": ["20280000"],
                "office_references": [],
                "value_date": datetime(2026, 8, 19),
            }
            with patch.object(
                module.engine,
                "indexed_month_directories",
                return_value=(expected, other),
            ):
                match = module.find_destination(metadata, root, datetime(2026, 8, 19))
            self.assertEqual(match["destination"], expected)
            self.assertEqual(match["score"], 100)

    def test_lote_unico_hereda_destino_del_oficio(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "BID-2201-OC-EC - 20280000"
            destination.mkdir()
            paths = []
            for name in ("oficio.pdf", "estado.pdf", "formulario.pdf"):
                path = root / name
                path.write_bytes(b"pdf")
                paths.append(path)
            items = [
                {
                    "path": paths[0],
                    "metadata": {"document_type": "OFICIO"},
                    "match": {
                        "destination": destination,
                        "score": 100,
                        "method": "Préstamo exacto",
                    },
                },
                {
                    "path": paths[1],
                    "metadata": {"document_type": "ESTADO CUENTA"},
                    "match": {"destination": None, "score": 0},
                },
                {
                    "path": paths[2],
                    "metadata": {"document_type": "FORMULARIO"},
                    "match": {"destination": None, "score": 0},
                },
            ]
            module.apply_batch_context(items)
            self.assertEqual(items[1]["match"]["destination"], destination)
            self.assertEqual(items[2]["match"]["destination"], destination)
            self.assertEqual(items[1]["match"]["score"], 99)

    def test_linea_base_no_procesa_archivos_historicos(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "app"
            source = Path(temporary) / "downloads"
            base.mkdir()
            source.mkdir()
            document = source / "documento_antiguo.pdf"
            document.write_bytes(b"historico")
            state = module.initialize_baseline(base, source)
            fingerprint = module.file_fingerprint(document)
            self.assertEqual(
                state["records"][fingerprint]["status"], "HISTÓRICO PROTEGIDO"
            )
            self.assertTrue(state["records"][fingerprint]["baseline"])

    def test_copia_verificada_y_conserva_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "estado_original.pdf"
            destination = root / "BID-2201-OC-EC - 20280000"
            destination.mkdir()
            source.write_bytes(b"contenido-pdf")
            item = {
                "path": source,
                "metadata": {
                    "document_type": "ESTADO CUENTA",
                    "office_references": [],
                    "loan_references": ["BID-2201-OC-EC"],
                    "sigade_numbers": ["20280000"],
                    "value_date": datetime(2026, 8, 19),
                    "amounts": [],
                    "currency": "USD",
                    "ocr_used": False,
                    "public_debt_payment": True,
                    "public_debt_evidence": ["préstamo de deuda pública"],
                },
                "match": {
                    "destination": destination,
                    "score": 100,
                    "method": "Préstamo exacto",
                    "evidence": ["referencia exacta"],
                    "notes": [],
                },
            }
            record = module._record_for(item, module.file_fingerprint(source))
            self.assertEqual(record["status"], "ARCHIVADO")
            self.assertTrue(source.exists())
            self.assertEqual(
                (destination / "Estado Cuenta.pdf").read_bytes(), b"contenido-pdf"
            )


if __name__ == "__main__":
    unittest.main()
