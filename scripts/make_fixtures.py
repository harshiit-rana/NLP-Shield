"""Generate synthetic image-only PDF fixtures with known planted PII.

Each fixture is rendered with PIL onto A4-at-300DPI images saved as an
image-only PDF (no text layer), with a JSON ground-truth file listing
every planted PII value. All PII here is synthetic: Aadhaar numbers carry
a valid Verhoeff check digit and card numbers a valid Luhn digit, but
none belong to a real person.

Usage: python scripts/make_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pseudonymiser.validators import luhn_generate, verhoeff_generate  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

A4 = (2480, 3508)  # 300 DPI
MARGIN = 220
LINE_HEIGHT = 78
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

# Synthetic identifiers with valid checksums (not real people).
AADHAAR_1 = verhoeff_generate("23456789012")      # 12 digits, Verhoeff-valid
AADHAAR_2 = verhoeff_generate("98765432109")
CARD_1 = luhn_generate("45390123456789")          # 15+1 digits, Luhn-valid
CARD_2 = luhn_generate("510512345678901")
AADHAAR_1_SPACED = f"{AADHAAR_1[:4]} {AADHAAR_1[4:8]} {AADHAAR_1[8:]}"
CARD_1_SPACED = f"{CARD_1[:4]} {CARD_1[4:8]} {CARD_1[8:12]} {CARD_1[12:]}"

FIXTURES: dict[str, dict] = {
    "contract": {
        "lines": [
            "SUPPLY CONTRACT No. HKB/2025-114",
            "",
            "This agreement is made between Rajesh Kumar Sharma,",
            "residing at 42 Nehru Marg, Jaipur, Rajasthan 302016,",
            "and Meera Krishnan of Chennai.",
            "",
            "Contact: rajesh.sharma@example.com, phone +91 9876543210.",
            "PAN: ABCPS1234F",
            "Total contract value: Rs. 1,495,000.00 payable on",
            "15 March 2025 by bank transfer.",
        ],
        "entities": [
            {"type": "REFERENCE_NUMBER", "value": "No. HKB/2025-114"},
            {"type": "PERSON", "value": "Rajesh Kumar Sharma"},
            {"type": "LOCATION", "value": "Jaipur"},
            {"type": "IN_PIN_CODE", "value": "302016"},
            {"type": "PERSON", "value": "Meera Krishnan"},
            {"type": "LOCATION", "value": "Chennai"},
            {"type": "EMAIL_ADDRESS", "value": "rajesh.sharma@example.com"},
            {"type": "PHONE_NUMBER", "value": "+91 9876543210"},
            {"type": "IN_PAN", "value": "ABCPS1234F"},
            {"type": "MONEY_AMOUNT", "value": "Rs. 1,495,000.00"},
            {"type": "DATE_TIME", "value": "15 March 2025"},
        ],
    },
    "kyc_form": {
        "lines": [
            "BANK OF FIXTURES - KYC VERIFICATION FORM",
            "",
            f"Applicant name: Priya Venkatesan",
            f"Aadhaar number: {AADHAAR_1_SPACED}",
            "Savings account number: 30112233445",
            "Branch IFSC code: HDFC0001234",
            f"Credit card: {CARD_1_SPACED}",
            "UPI ID for refunds: priya.v@okhdfc",
            "Registered mobile: 9812345670",
            "Address: 7 MG Road, Bengaluru, Karnataka",
            "PIN code: 560001",
        ],
        "entities": [
            {"type": "PERSON", "value": "Priya Venkatesan"},
            {"type": "IN_AADHAAR", "value": AADHAAR_1_SPACED},
            {"type": "BANK_ACCOUNT", "value": "30112233445"},
            {"type": "IN_IFSC", "value": "HDFC0001234"},
            {"type": "CREDIT_CARD", "value": CARD_1_SPACED},
            {"type": "IN_UPI", "value": "priya.v@okhdfc"},
            {"type": "PHONE_NUMBER", "value": "9812345670"},
            {"type": "LOCATION", "value": "Bengaluru"},
            {"type": "IN_PIN_CODE", "value": "560001"},
        ],
    },
    "letter": {
        "lines": [
            "Dear Mr. Arun Patel,",
            "",
            "Thank you for your enquiry dated 2 January 2026.",
            "Our representative Sunita Rao will visit your office",
            "at 15 Park Street, Kolkata next week.",
            "",
            "Server access will be from IP 192.168.10.45 and our",
            "portal https://portal.example.org remains available.",
            "For payment queries call 044-28123456 or e-mail",
            "accounts@fixtures.example.in quoting TIN 123-456-789-000.",
            "Yours sincerely,",
            "David Fernandes",
        ],
        "entities": [
            {"type": "PERSON", "value": "Arun Patel"},
            {"type": "DATE_TIME", "value": "2 January 2026"},
            {"type": "PERSON", "value": "Sunita Rao"},
            {"type": "LOCATION", "value": "Kolkata"},
            {"type": "IP_ADDRESS", "value": "192.168.10.45"},
            {"type": "URL", "value": "https://portal.example.org"},
            {"type": "PHONE_NUMBER", "value": "044-28123456"},
            {"type": "EMAIL_ADDRESS", "value": "accounts@fixtures.example.in"},
            {"type": "PH_TIN", "value": "123-456-789-000"},
            {"type": "PERSON", "value": "David Fernandes"},
        ],
    },
    "nda": {
        # TPRM-style legal document: the primary real-world input.
        "lines": [
            "MUTUAL NON-DISCLOSURE AGREEMENT",
            "",
            "This Agreement is entered into between Hardkraft Boxes",
            "Private Limited, CIN U74999MH2018PTC312456, having its",
            "registered office in Mumbai, and Tinycrows Technologies",
            "LLP, GSTIN 27ABCPS1234F1Z5, of Pune.",
            "",
            "Signed for and on behalf of the parties by",
            "Ananya Deshpande (Director) and Karan Mehta (Partner)",
            "on 12 June 2025. Notices shall be sent to",
            "legal@hardkraft.example.com.",
        ],
        "entities": [
            {"type": "ORGANIZATION", "value": "Hardkraft Boxes"},
            {"type": "IN_CIN", "value": "U74999MH2018PTC312456"},
            {"type": "LOCATION", "value": "Mumbai"},
            {"type": "ORGANIZATION", "value": "Tinycrows Technologies"},
            {"type": "IN_GSTIN", "value": "27ABCPS1234F1Z5"},
            {"type": "LOCATION", "value": "Pune"},
            {"type": "PERSON", "value": "Ananya Deshpande"},
            {"type": "PERSON", "value": "Karan Mehta"},
            {"type": "DATE_TIME", "value": "12 June 2025"},
            {"type": "EMAIL_ADDRESS", "value": "legal@hardkraft.example.com"},
        ],
    },
    "skewed_invoice": {
        # Rendered with a small rotation to exercise deskew.
        "rotate": 2.0,
        "lines": [
            "TAX INVOICE - Hardkraft Boxes Pvt Ltd",
            "",
            "Bill to: Vikram Singh Chauhan",
            "GST state: Maharashtra, Mumbai 400001",
            f"Aadhaar: {AADHAAR_2}",
            f"Card on file: {CARD_2}",
            "Contact: vikram.chauhan@example.com / +91-9123456789",
            "Amount due: Rs. 78,500.00",
        ],
        "entities": [
            {"type": "PERSON", "value": "Vikram Singh Chauhan"},
            {"type": "LOCATION", "value": "Mumbai"},
            {"type": "IN_PIN_CODE", "value": "400001"},
            {"type": "IN_AADHAAR", "value": AADHAAR_2},
            {"type": "CREDIT_CARD", "value": CARD_2},
            {"type": "EMAIL_ADDRESS", "value": "vikram.chauhan@example.com"},
            {"type": "PHONE_NUMBER", "value": "+91-9123456789"},
            {"type": "MONEY_AMOUNT", "value": "Rs. 78,500.00"},
        ],
    },
}


def _font(size: int = 56) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise SystemExit("No usable TTF font found; install dejavu fonts")


def render_fixture(name: str, spec: dict) -> None:
    img = Image.new("RGB", A4, "white")
    draw = ImageDraw.Draw(img)
    font = _font()
    y = MARGIN
    for line in spec["lines"]:
        draw.text((MARGIN, y), line, fill="black", font=font)
        y += LINE_HEIGHT
    if spec.get("rotate"):
        img = img.rotate(spec["rotate"], expand=True, fillcolor="white")

    pdf_path = FIXTURES_DIR / f"{name}.pdf"
    img.save(pdf_path, "PDF", resolution=300.0)

    truth = {"fixture": name, "entities": spec["entities"]}
    (FIXTURES_DIR / f"{name}.truth.json").write_text(
        json.dumps(truth, indent=2), encoding="utf-8"
    )
    print(f"wrote {pdf_path.name} + ground truth ({len(spec['entities'])} entities)")


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for name, spec in FIXTURES.items():
        render_fixture(name, spec)


if __name__ == "__main__":
    main()
