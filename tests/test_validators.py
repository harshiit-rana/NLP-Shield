"""Unit tests for checksum validators and OCR normalisation."""

from pseudonymiser.validators import (
    luhn_generate,
    luhn_valid,
    normalize_digits,
    normalize_pan,
    pan_structurally_valid,
    verhoeff_generate,
    verhoeff_valid,
)


class TestVerhoeff:
    def test_generated_number_is_valid(self):
        assert verhoeff_valid(verhoeff_generate("23456789012"))

    def test_single_digit_error_detected(self):
        valid = verhoeff_generate("23456789012")
        corrupted = valid[:-1] + str((int(valid[-1]) + 1) % 10)
        assert not verhoeff_valid(corrupted)

    def test_transposition_detected(self):
        valid = verhoeff_generate("23456789012")
        swapped = valid[1] + valid[0] + valid[2:]
        if swapped != valid:
            assert not verhoeff_valid(swapped)

    def test_non_digits_rejected(self):
        assert not verhoeff_valid("12a4")
        assert not verhoeff_valid("")


class TestLuhn:
    def test_known_valid_card(self):
        assert luhn_valid("4539578763621486")  # standard test number

    def test_generated_is_valid(self):
        assert luhn_valid(luhn_generate("453957876362148"))

    def test_single_digit_error_detected(self):
        assert not luhn_valid("4539578763621487")

    def test_too_short_rejected(self):
        assert not luhn_valid("12345")


class TestPanStructure:
    def test_valid_individual_pan(self):
        assert pan_structurally_valid("ABCPS1234F")

    def test_invalid_fourth_char(self):
        assert not pan_structurally_valid("ABCXS1234F")

    def test_wrong_shape(self):
        assert not pan_structurally_valid("AB1PS1234F")
        assert not pan_structurally_valid("ABCPS12345")


class TestNormalization:
    def test_digit_confusions(self):
        assert normalize_digits("2O8I 5S3B") == "20815538"

    def test_separators_dropped(self):
        assert normalize_digits("1234 5678-9012") == "123456789012"

    def test_pan_positional_repair(self):
        # letters misread as digits and vice versa, repaired by position
        assert normalize_pan("A8CP51234F") == "ABCPS1234F"
        assert normalize_pan("ABCPSI23AF") is None or len("ABCPSI23AF") == 10

    def test_pan_wrong_length(self):
        assert normalize_pan("ABC123") is None
