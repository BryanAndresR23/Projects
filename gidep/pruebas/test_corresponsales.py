from __future__ import annotations

import unittest

from corresponsales_module import classify_correspondent, extract_beneficiary_bank


EMPTY_HISTORY = {"by_loan": {}, "by_profile": {}, "by_lender": {}}


class CorrespondentClassifierTests(unittest.TestCase):
    def test_citibank_beneficiary_has_priority(self):
        result = classify_correspondent(
            loan_reference="BIRF-9595-EC",
            lender="BIRF",
            borrower="MEF",
            currency="USD",
            beneficiary_bank="CITIBANK N.A.",
            history=EMPTY_HISTORY,
        )
        self.assertEqual(result["correspondent"], "CITIBANK")
        self.assertEqual(result["confidence"], 100)

    def test_currency_rules(self):
        eur = classify_correspondent(
            loan_reference="BEI-1234-EC", lender="BEI", borrower="MEF",
            currency="EUR", history=EMPTY_HISTORY,
        )
        jpy = classify_correspondent(
            loan_reference="JICA-1234-EC", lender="JICA", borrower="MEF",
            currency="JPY", history=EMPTY_HISTORY,
        )
        self.assertEqual(eur["correspondent"], "BANCO DE ESPAÑA")
        self.assertEqual(jpy["correspondent"], "COMMERZBANK")

    def test_exact_loan_history_is_used(self):
        result = classify_correspondent(
            loan_reference="BID-3913-OC-EC",
            lender="BID",
            borrower="MEF",
            currency="USD",
            history={
                "by_loan": {"BID 3913 OC": {"FEDERAL": 4}},
                "by_profile": {},
                "by_lender": {},
            },
        )
        self.assertEqual(result["correspondent"], "FEDERAL")
        self.assertEqual(result["method"], "Préstamo exacto en matriz")

    def test_extracts_bank_from_account_statement(self):
        self.assertEqual(
            extract_beneficiary_bank("Banco del beneficiario: Citibank N.A. CITIUS33"),
            "CITIBANK N.A.",
        )

    def test_republic_of_ecuador_usd_uses_federal_despite_bny_or_history(self):
        result = classify_correspondent(
            loan_reference="BID-2487-OC-EC",
            lender="BID",
            borrower="República del Ecuador",
            currency="USD",
            beneficiary_bank="THE BANK OF NEW YORK MELLON",
            history={
                "by_loan": {"BID 2487 OC": {"JPMORGAN": 5}},
                "by_profile": {},
                "by_lender": {},
            },
        )
        self.assertEqual(result["correspondent"], "FEDERAL")
        self.assertEqual(result["confidence"], 100)
        self.assertEqual(result["method"], "República del Ecuador + guía")

    def test_mdep_is_treated_as_sovereign_borrower(self):
        result = classify_correspondent(
            loan_reference="BID-2487-OC-EC",
            lender="BID",
            borrower="MDEP",
            currency="USD",
            beneficiary_bank="THE BANK OF NEW YORK MELLON",
            history=EMPTY_HISTORY,
        )
        self.assertEqual(result["correspondent"], "FEDERAL")


if __name__ == "__main__":
    unittest.main()
