"""Regresión del hallazgo BID-1740: pagos que desaparecían de la agenda.

Contexto
--------
El oficio ``MDEP-SFPAR-2026-0824-O`` del préstamo BID-1740 quedó registrado en
``agenda_cache.json`` como ``{"ocr_complete": true, "candidate": null}``. El
pago nunca apareció en la agenda diaria y el descarte era permanente: al no
cambiar el PDF, ``cached_no_candidate`` seguía devolviendo ``True`` y el
documento jamás se reintentaba, ni siquiera al pulsar "Refrescar".

Causa raíz: ``extract_value_date`` era el único filtro de ``office_candidate``.
Si no reconocía la redacción de la fecha, se descartaba el documento completo
—préstamo, monto, moneda y beneficiario incluidos—.

Estas pruebas cubren las tres correcciones.
"""

from datetime import date, timedelta
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "codigo"))

import agenda_pagos_module as agenda  # noqa: E402


class ExtraccionFechaValor(unittest.TestCase):
    """Redacciones que el extractor debe reconocer."""

    def test_conserva_rotulos_historicos(self):
        """Los cuatro patrones previos siguen funcionando igual."""
        casos = [
            ("Fecha valor: 15 de septiembre de 2026", "Fecha valor"),
            ("Fecha de débito: 15 de septiembre de 2026", "Fecha de débito"),
            ("Fecha valor al 15 de septiembre de 2026", "Fecha valor"),
        ]
        for texto, etiqueta in casos:
            with self.subTest(texto=texto):
                fecha, tipo = agenda.extract_value_date(texto)
                self.assertEqual(fecha, date(2026, 9, 15))
                self.assertEqual(tipo, etiqueta)

    def test_reconoce_redaccion_mdep_sirvase_debitar(self):
        """Redacción del oficio que dejó fuera a BID-1740."""
        texto = (
            "Sírvase debitar de la cuenta corriente única del Tesoro Nacional "
            "el 15 de septiembre de 2026 el valor correspondiente."
        )
        fecha, tipo = agenda.extract_value_date(texto)
        self.assertEqual(fecha, date(2026, 9, 15))
        self.assertEqual(tipo, "Fecha de débito")

    def test_reconoce_fecha_de_pago(self):
        fecha, tipo = agenda.extract_value_date("Fecha de pago: 15 de septiembre de 2026")
        self.assertEqual(fecha, date(2026, 9, 15))
        self.assertEqual(tipo, "Fecha de pago")

    def test_reconoce_el_dia_con_debito_posterior(self):
        texto = "Se solicita que el día 15 de septiembre de 2026 se debite la cuenta."
        fecha, _ = agenda.extract_value_date(texto)
        self.assertEqual(fecha, date(2026, 9, 15))

    def test_ignora_la_fecha_de_membrete(self):
        """La fecha del membrete nunca debe tomarse como fecha valor.

        Sin esta guarda, el patrón laxo de débito tomaría la fecha del
        encabezado del oficio y programaría el pago en el día equivocado.
        """
        texto = (
            "Quito, D.M., 01 de septiembre de 2026\n"
            "Oficio No. MDEP-SFPAR-2026-0824-O\n"
            "Sírvase debitar de la cuenta el 15 de septiembre de 2026."
        )
        fecha, _ = agenda.extract_value_date(texto)
        self.assertEqual(fecha, date(2026, 9, 15), "tomó la fecha del membrete")

    def test_el_rotulo_explicito_gana_al_patron_laxo(self):
        texto = (
            "Sírvase debitar la cuenta el 10 de septiembre de 2026. "
            "Fecha valor: 15 de septiembre de 2026."
        )
        fecha, tipo = agenda.extract_value_date(texto)
        self.assertEqual(fecha, date(2026, 9, 15))
        self.assertEqual(tipo, "Fecha valor")

    def test_sin_contexto_de_pago_no_inventa_fecha(self):
        """Una fecha suelta sin contexto operativo no es fecha valor."""
        fecha, tipo = agenda.extract_value_date(
            "El contrato fue suscrito el 15 de septiembre de 2026."
        )
        self.assertIsNone(fecha)
        self.assertEqual(tipo, "")


class DescarteNoPermanente(unittest.TestCase):
    """El descarte de un documento ilegible debe caducar."""

    def setUp(self):
        agenda._candidate_cache = {}
        self.addCleanup(setattr, agenda, "_candidate_cache", None)

    def _entrada(self, ruta, checked_at):
        stat = ruta.stat()
        entrada = {
            "modified_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "ocr_complete": True,
            "candidate": None,
        }
        if checked_at is not None:
            entrada["checked_at"] = checked_at
        agenda._candidate_cache[str(ruta)] = entrada

    def test_descarte_reciente_se_respeta(self):
        ruta = Path(__file__)
        self._entrada(ruta, time.time())
        self.assertTrue(agenda.cached_no_candidate(ruta))

    def test_descarte_caducado_se_reintenta(self):
        ruta = Path(__file__)
        vencido = time.time() - agenda.NO_CANDIDATE_TTL_SECONDS - 60
        self._entrada(ruta, vencido)
        self.assertFalse(
            agenda.cached_no_candidate(ruta),
            "un descarte vencido debe reintentarse",
        )

    def test_descarte_sin_marca_de_tiempo_se_reintenta(self):
        """Las 51 entradas atascadas por la versión anterior se auto-reparan."""
        ruta = Path(__file__)
        self._entrada(ruta, None)
        self.assertFalse(
            agenda.cached_no_candidate(ruta),
            "las entradas heredadas deben reintentarse una vez",
        )

    def test_contador_de_descartados_es_visible(self):
        ruta = Path(__file__)
        self._entrada(ruta, time.time())
        self.assertEqual(agenda.discarded_document_count(), 1)


class PagoSinFechaVaARevisar(unittest.TestCase):
    """Un oficio sin fecha legible no debe desaparecer en silencio."""

    def setUp(self):
        agenda._candidate_cache = {}
        self.addCleanup(setattr, agenda, "_candidate_cache", None)

    def _pago(self, **cambios):
        base = dict(
            payment_id="BID|Pagos|BID-1740",
            responsible="BID",
            voucher_number="",
            source_folder="",
            source_document="",
            document_name="Oficio No. MDEP-SFPAR-2026-0824-O.pdf",
            office_reference="MDEP-SFPAR-2026-0824-O",
            issuer="MDEP",
            loan_reference="BID-1740",
            loan_folder="BID-1740-OC-EC - 20269000 - Municipio Quito",
            value_date="",
            date_type="Sin fecha legible",
            due_date=None,
            amount_value="1,292,372.42",
            currency="USD",
            status="review",
            timing="undated",
            days_until=0,
            completed=False,
            accounting_ready=False,
            operation_reference=None,
            confidence=60,
            ocr_used=True,
            note="No se pudo leer la fecha valor/débito en el oficio.",
        )
        base.update(cambios)
        return agenda.ScheduledPayment(**base)

    def test_no_se_cuenta_como_pago_de_hoy(self):
        """days_until=0 no debe inflar el contador de pagos del día."""
        sin_fecha = self._pago()
        hoy = self._pago(
            payment_id="BID|Pagos|BID-5676",
            value_date=date.today().isoformat(),
            date_type="Fecha valor",
            timing="today",
            status="today",
        )
        totales = agenda.totals_by_currency([sin_fecha, hoy], today_only=True)
        self.assertEqual(
            totales.get("USD"),
            agenda.amount_number("1,292,372.42"),
            "el monto sin fecha no debe sumarse al total del día",
        )

    def test_totales_del_dia_excluyen_los_sin_fecha(self):
        solo_sin_fecha = agenda.totals_by_currency([self._pago()], today_only=True)
        self.assertEqual(solo_sin_fecha, {})


class CandidatoSinFecha(unittest.TestCase):
    """``office_candidate`` conserva el documento si hay préstamo o monto."""

    def setUp(self):
        agenda._candidate_cache = {}
        self.addCleanup(setattr, agenda, "_candidate_cache", None)

    def test_emite_candidato_sin_fecha_cuando_hay_prestamo(self):
        texto = (
            "Oficio No. MDEP-SFPAR-2026-0824-O\n"
            "Préstamo BID-1740-OC-EC\n"
            "Monto de USD 1,292,372.42\n"
            "Se solicita atender el requerimiento."
        )
        ruta = Path(__file__)
        original = agenda.searchable_text
        agenda.searchable_text = lambda path, use_ocr=True: (texto, False)
        self.addCleanup(setattr, agenda, "searchable_text", original)

        candidato = agenda.office_candidate(ruta, use_ocr=True)

        self.assertIsNotNone(candidato, "el documento no debe descartarse")
        self.assertIsNone(candidato["value_date"])
        self.assertEqual(candidato["date_type"], "Sin fecha legible")
        self.assertTrue(
            str(candidato["loan"]).startswith("BID-1740"),
            f"préstamo no identificado: {candidato['loan']!r}",
        )
        self.assertEqual(candidato["amount"], "1,292,372.42")

    def test_descarta_cuando_no_hay_prestamo_ni_monto(self):
        ruta = Path(__file__)
        original = agenda.searchable_text
        agenda.searchable_text = lambda path, use_ocr=True: ("Documento sin datos.", False)
        self.addCleanup(setattr, agenda, "searchable_text", original)

        self.assertIsNone(agenda.office_candidate(ruta, use_ocr=True))
        self.assertTrue(agenda.cached_no_candidate(ruta))


if __name__ == "__main__":
    unittest.main()
