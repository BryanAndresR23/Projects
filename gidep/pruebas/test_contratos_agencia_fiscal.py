import os
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import contratos_agencia_fiscal_module as module


FINAL_KFW_OFFICE = """
Oficio Nro. BCE-SSFI-2026-0792-OF
Quito, 28 de agosto de 2026
Asunto: Elaboración de Contrato de Agencia Fiscal - Contrato de Préstamo
KFW - BMZ-No.2021.6517.3
Señora
Gerente General
CORPORACIÓN FINANCIERA NACIONAL B.P. - CFN
De mi consideración:
En atención al Contrato de Préstamo BMZ-No.2021.6517.3 suscrito con KfW,
se adjunta el borrador del Contrato de Agencia Fiscal para su revisión.
El proyecto fue elaborado mediante BCE-GISI-2026-0431-M y revisado mediante
BCE-GJ-2026-0454-M.
Documento firmado electrónicamente por Quipux
"""


def make_kfw_repository(root: Path, suffix: str = "") -> Path:
    folder = (
        root
        / "GOBIERNOS"
        / "Contratos"
        / "Contratos Agencia Fiscal"
        / "KFW"
        / f"KFW-BMZ-02165173 - 23222000 - CFN{suffix}"
    )
    folder.mkdir(parents=True)
    (folder / "Borrador Contrato Agencia Fiscal - KFW No. 2021.6517.3.doc").write_bytes(b"doc")
    (folder / "BCE-GISI-2026-0431-M (Elaboración Contrato Agencia Fiscal).pdf").write_bytes(b"old")
    (folder / "BCE-GJ-2026-0454-M (Envío Borrador Contrato).pdf").write_bytes(b"old2")
    (folder / "BCE-SSFI-2026-0001-OF (Envío Borrador Contrato).pdf").write_bytes(b"old3")
    return folder


class ContractOfficeRulesTests(unittest.TestCase):
    def test_accepts_only_final_signed_contract_office(self):
        path = Path("BCE-SSFI-2026-0792-OF.pdf")
        result = module.classify_contract_office(path, FINAL_KFW_OFFICE)
        self.assertTrue(result["candidate"])
        self.assertTrue(result["signature_verified"])
        self.assertEqual(result["office_reference"], "BCE-SSFI-2026-0792-OF")
        self.assertIn("Contrato de Préstamo", result["subject"])

    def test_excludes_temp_memo_and_payment_office(self):
        temp_text = FINAL_KFW_OFFICE.replace(
            "Oficio Nro. BCE-SSFI-2026-0792-OF",
            "Oficio Nro. BCE-SSFI-2026-1274-TEMP",
        ).replace("Documento firmado electrónicamente", "Documento generado")
        temp = module.classify_contract_office(
            Path("BCE-SSFI-2026-1274-TEMP-3.pdf"), temp_text
        )
        self.assertFalse(temp["candidate"])
        self.assertTrue(temp["is_temp"])

        memo = module.classify_contract_office(
            Path("BCE-GJ-2026-0454-M.pdf"),
            FINAL_KFW_OFFICE.replace(
                "Oficio Nro. BCE-SSFI-2026-0792-OF",
                "Memorando Nro. BCE-GJ-2026-0454-M",
            ),
        )
        self.assertFalse(memo["candidate"])
        self.assertFalse(memo["is_office"])

        memo_with_attached_office = module.classify_contract_office(
            Path("BCE-GJ-2026-0454-M.pdf"),
            """Memorando Nro. BCE-GJ-2026-0454-M
            Asunto: Proyecto de Contrato de Agencia Fiscal KFW
            En atención al Oficio Nro. CFN-B.P.-SGNF-2026-0065-O se revisó el
            Contrato de Agencia Fiscal. Documento firmado electrónicamente por Quipux""",
        )
        self.assertFalse(memo_with_attached_office["candidate"])
        self.assertFalse(memo_with_attached_office["is_office"])

        payment = module.classify_contract_office(
            Path("MDEP-STN-2026-2000-O.pdf"),
            """Oficio Nro. MDEP-STN-2026-2000-O
            Asunto: Pago de intereses del préstamo KFW
            Fecha valor 28/08/2026. Documento firmado electrónicamente por Quipux""",
        )
        self.assertFalse(payment["candidate"])
        self.assertFalse(payment["contract_relevant"])

    def test_matches_current_kfw_folder_and_learns_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = make_kfw_repository(root)
            folders = module.discover_contract_folders(root)
            learned = module.learned_descriptor_patterns(folders)
            metadata = module.extract_metadata(
                Path("BCE-SSFI-2026-0792-OF.pdf"), FINAL_KFW_OFFICE, False
            )
            match = module.find_destination(metadata, folders)
            descriptor, method, evidence = module.choose_descriptor(
                metadata["subject"], FINAL_KFW_OFFICE, learned
            )
            self.assertEqual(match["destination"], expected)
            self.assertEqual(match["score"], 100)
            self.assertIn("contrato exacto", " ".join(match["evidence"]))
            self.assertEqual(descriptor, "Envío Borrador Contrato")
            self.assertIn("Patrón aprendido", method)
            self.assertTrue(evidence)

    def test_ambiguous_contract_is_never_auto_archived(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_kfw_repository(root)
            make_kfw_repository(root, " (2)")
            metadata = module.extract_metadata(
                Path("BCE-SSFI-2026-0792-OF.pdf"), FINAL_KFW_OFFICE, False
            )
            match = module.find_destination(
                metadata, module.discover_contract_folders(root)
            )
            self.assertIsNone(match["destination"])
            self.assertEqual(match["method"], "Coincidencia ambigua")


class ContractOfficeScanTests(unittest.TestCase):
    def setUp(self):
        module._runtime.update(
            {"last_scan": None, "last_error": None, "monitor_status": "INICIANDO"}
        )

    def test_scan_copies_with_verified_institutional_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "app"
            source = Path(temporary) / "downloads"
            destination = Path(temporary) / "archive"
            base.mkdir()
            source.mkdir()
            contract_folder = make_kfw_repository(destination)
            pdf = source / "BCE-SSFI-2026-0792-OF.pdf"
            pdf.write_bytes(b"%PDF-1.4\ncontract-office-test\n%%EOF")
            os.utime(pdf, (pdf.stat().st_atime - 30, pdf.stat().st_mtime - 30))
            module.save_config(
                base,
                {
                    "source": str(source),
                    "destination": str(destination),
                    "use_ocr": True,
                    "automatic": False,
                },
            )
            expected = contract_folder / (
                "BCE-SSFI-2026-0792-OF (Envío Borrador Contrato).pdf"
            )
            with patch.object(
                module.engine,
                "searchable_pdf_text",
                return_value=(FINAL_KFW_OFFICE, False),
            ), patch.object(module, "STABLE_AGE_SECONDS", 0), patch.object(
                module,
                "configure_logging",
                return_value=logging.getLogger("test.contratos-agencia"),
            ):
                payload = module.scan_once(base, origin="PRUEBA")
            self.assertIsNone(payload["last_error"])
            self.assertTrue(expected.is_file())
            self.assertEqual(expected.read_bytes(), pdf.read_bytes())
            self.assertEqual(payload["summary"]["archived"], 1)
            self.assertEqual(payload["records"][0]["status"], "ARCHIVADO")
            self.assertTrue(pdf.is_file(), "El original debe conservarse")


if __name__ == "__main__":
    unittest.main()
