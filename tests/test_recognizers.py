"""Unit tests for custom pattern recognisers and the OCR-tolerant
recogniser. These run without any NLP model: pattern recognisers work
standalone (context boosting is exercised in the integration tests)."""

import pytest

from pseudonymiser.detect import OcrTolerantRecognizer, build_custom_recognizers
from pseudonymiser.validators import luhn_generate, verhoeff_generate


@pytest.fixture(scope="module")
def recognizers():
    return {r.supported_entities[0]: r for r in build_custom_recognizers()}


def hits(recognizer, text, entity):
    return [text[r.start:r.end] for r in recognizer.analyze(text, [entity])]


class TestIfsc:
    def test_matches(self, recognizers):
        assert "HDFC0001234" in hits(recognizers["IN_IFSC"],
                                     "IFSC: HDFC0001234", "IN_IFSC")

    def test_rejects_wrong_shape(self, recognizers):
        # 5th character must be the literal 0 of the IFSC format
        assert not hits(recognizers["IN_IFSC"], "HDFC1001234", "IN_IFSC")
        assert not hits(recognizers["IN_IFSC"], "HDF0001234", "IN_IFSC")


class TestUpi:
    def test_matches_vpa(self, recognizers):
        assert "priya.v@okhdfc" in hits(recognizers["IN_UPI"],
                                        "pay priya.v@okhdfc today", "IN_UPI")

    def test_rejects_email_with_tld(self, recognizers):
        assert not hits(recognizers["IN_UPI"], "mail me at a.b@example.com",
                        "IN_UPI")


class TestIndianMobile:
    def test_with_country_code(self, recognizers):
        assert hits(recognizers["PHONE_NUMBER"],
                    "call +91 98765 43210 now", "PHONE_NUMBER")

    def test_bare_ten_digit(self, recognizers):
        assert hits(recognizers["PHONE_NUMBER"],
                    "mobile 9876543210", "PHONE_NUMBER")

    def test_rejects_leading_below_six(self, recognizers):
        assert not hits(recognizers["PHONE_NUMBER"],
                        "value 1234567890", "PHONE_NUMBER")


class TestContextGatedNumbers:
    """PIN and account patterns carry a low base score by design; they only
    cross the threshold via context words (verified in integration tests).
    Here we just confirm the pattern shapes."""

    def test_pin_shape(self, recognizers):
        assert hits(recognizers["IN_PIN_CODE"], "PIN 560001", "IN_PIN_CODE")
        assert not hits(recognizers["IN_PIN_CODE"], "PIN 060001", "IN_PIN_CODE")

    def test_account_shape(self, recognizers):
        assert hits(recognizers["BANK_ACCOUNT"], "a/c 30112233445", "BANK_ACCOUNT")
        assert not hits(recognizers["BANK_ACCOUNT"], "a/c 12345678", "BANK_ACCOUNT")


class TestVehicleRegistration:
    def test_matches_plate(self, recognizers):
        assert "MH12AB1234" in hits(recognizers["IN_VEHICLE_REGISTRATION"],
                                    "vehicle MH12AB1234", "IN_VEHICLE_REGISTRATION")
        assert hits(recognizers["IN_VEHICLE_REGISTRATION"],
                    "reg KA-05-MJ-6789", "IN_VEHICLE_REGISTRATION")

    def test_rejects_unknown_state(self, recognizers):
        assert not hits(recognizers["IN_VEHICLE_REGISTRATION"],
                        "code XX12AB1234", "IN_VEHICLE_REGISTRATION")


class TestCorporateIds:
    def test_gstin(self, recognizers):
        assert "27ABCPS1234F1Z5" in hits(recognizers["IN_GSTIN"],
                                         "GSTIN 27ABCPS1234F1Z5", "IN_GSTIN")
        assert not hits(recognizers["IN_GSTIN"], "27ABCPS1234F1X5", "IN_GSTIN")

    def test_cin(self, recognizers):
        assert "U74999MH2018PTC312456" in hits(
            recognizers["IN_CIN"], "CIN U74999MH2018PTC312456", "IN_CIN")
        assert not hits(recognizers["IN_CIN"], "X74999MH2018PTC312456", "IN_CIN")


class TestLegacyRecognizers:
    def test_ph_tin(self, recognizers):
        assert "123-456-789-000" in hits(recognizers["PH_TIN"],
                                         "TIN 123-456-789-000", "PH_TIN")

    def test_reference_number(self, recognizers):
        assert hits(recognizers["REFERENCE_NUMBER"],
                    "Public Bidding No. 2017-02", "REFERENCE_NUMBER")

    def test_money(self, recognizers):
        found = hits(recognizers["MONEY_AMOUNT"],
                     "total Rs. 1,495,000.00 due", "MONEY_AMOUNT")
        assert any("1,495,000" in f for f in found)

    def test_money_requires_a_digit(self, recognizers):
        # regression: "P" followed by a bare comma is not an amount
        assert not hits(recognizers["MONEY_AMOUNT"], "TIONG Op, ROLL",
                        "MONEY_AMOUNT")


@pytest.fixture(scope="module")
def street():
    for r in build_custom_recognizers():
        if r.name == "StreetAddressRecognizer":
            return r
    raise AssertionError("StreetAddressRecognizer not registered")


class TestStreetAddress:

    def test_matches_street_lines(self, street):
        assert hits(street, "at 1031 A. Mabini Street, Ermita", "LOCATION")
        assert hits(street, "office: 42 Nehru Marg", "LOCATION")
        assert hits(street, "1031 A. Mabini St. Ermita", "LOCATION")

    def test_ignores_plain_numbers(self, street):
        assert not hits(street, "consists of 3 pages in total", "LOCATION")


@pytest.fixture(scope="module")
def rec():
    return OcrTolerantRecognizer()


class TestOcrTolerantRecognizer:

    def test_clean_aadhaar(self, rec):
        aadhaar = verhoeff_generate("23456789012")
        results = rec.analyze(f"Aadhaar {aadhaar}", ["IN_AADHAAR"])
        assert len(results) == 1

    def test_garbled_aadhaar_recovered(self, rec):
        aadhaar = verhoeff_generate("23456789012")
        garbled = aadhaar.replace("0", "O", 1).replace("1", "l", 1)
        results = rec.analyze(f"Aadhaar {garbled}", ["IN_AADHAAR"])
        assert len(results) == 1

    def test_invalid_checksum_rejected(self, rec):
        aadhaar = verhoeff_generate("23456789012")
        bad = aadhaar[:-1] + str((int(aadhaar[-1]) + 1) % 10)
        assert rec.analyze(f"Aadhaar {bad}", ["IN_AADHAAR"]) == []

    def test_garbled_card_recovered(self, rec):
        card = luhn_generate("453957876362148")
        garbled = card[:4] + "S" + card[5:]  # 5 -> S confusion
        if card[4] == "5":
            results = rec.analyze(f"card {garbled}", ["CREDIT_CARD"])
            assert len(results) == 1

    def test_random_digits_rejected(self, rec):
        assert rec.analyze("code 4539578763621487", ["CREDIT_CARD"]) == []

    def test_garbled_pan_recovered(self, rec):
        results = rec.analyze("PAN A8CP51234F", ["IN_PAN"])
        assert len(results) == 1
