import re
from dataclasses import dataclass

from app.schemas import LeadFields


AADHAAR_RE = re.compile(r"\b(?:\d[ -]?){12}\b")
PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", re.IGNORECASE)
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
OTP_RE = re.compile(r"\b(?:otp|one[- ]?time password)\b", re.IGNORECASE)

SENSITIVE_KEYWORDS = {
    "aadhaar",
    "aadhar",
    "pan card",
    "permanent account number",
    "otp",
    "one-time password",
    "one time password",
    "bank account",
    "account number",
    "ifsc",
    "swift code",
    "credit card",
    "debit card",
    "card number",
    "cvv",
    "cvc",
    "card expiry",
    "expiry date",
    "upi pin",
}

DOCUMENT_KEYWORDS = {
    "medical report",
    "lab report",
    "diagnostic report",
    "hospital report",
    "prescription",
}

COLLECTION_WORDS = {
    "upload",
    "attach",
    "send",
    "share",
    "submit",
    "provide",
    "here is",
    "my ",
}

SENSITIVE_RESPONSE = (
    "Please do not share Aadhaar, PAN, OTP, bank details, card details, medical reports, "
    "or prescriptions here. I can still help with health insurance questions, quotes, "
    "callbacks, and claims using basic contact and plan preference details only."
)


@dataclass(frozen=True)
class SafetyCheck:
    blocked: bool
    flags: list[str]


def detect_sensitive_input(text: str) -> SafetyCheck:
    flags: list[str] = []
    lowered = text.lower()

    for keyword in SENSITIVE_KEYWORDS:
        if keyword in lowered:
            flags.append(keyword)

    if any(keyword in lowered for keyword in DOCUMENT_KEYWORDS) and any(
        word in lowered for word in COLLECTION_WORDS
    ):
        flags.append("medical_document")

    if AADHAAR_RE.search(text):
        flags.append("aadhaar_number")
    if PAN_RE.search(text):
        flags.append("pan_number")
    if OTP_RE.search(text):
        flags.append("otp")
    if _looks_like_payment_card(text):
        flags.append("card_number")

    return SafetyCheck(blocked=bool(flags), flags=sorted(set(flags)))


def _looks_like_payment_card(text: str) -> bool:
    for match in CARD_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) >= 13 and _luhn_checksum(digits):
            return True
    return False


def _luhn_checksum(number: str) -> bool:
    total = 0
    reverse_digits = number[::-1]
    for index, char in enumerate(reverse_digits):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def redact_sensitive_text(value: str | None) -> str | None:
    if value is None:
        return None
    redacted = AADHAAR_RE.sub("[REDACTED_AADHAAR]", value)
    redacted = PAN_RE.sub("[REDACTED_PAN]", redacted)
    redacted = CARD_RE.sub("[REDACTED_NUMBER]", redacted)
    redacted = OTP_RE.sub("[REDACTED_OTP]", redacted)
    return redacted.strip() or None


def sanitize_lead_fields(fields: LeadFields) -> LeadFields:
    data = fields.model_dump()
    for key, value in data.items():
        if isinstance(value, str):
            data[key] = redact_sensitive_text(value)

    if data.get("pre_existing_condition"):
        check = detect_sensitive_input(data["pre_existing_condition"])
        if check.blocked:
            data["pre_existing_condition"] = None

    return LeadFields.model_validate(data)


def has_contact_details(fields: LeadFields) -> bool:
    return bool(fields.phone or fields.email)
