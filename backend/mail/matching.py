"""Tell whether two mentions of an employer and role mean the same application."""

import re
import unicodedata

LEGAL_WORDS = {
    "ag", "bv", "co", "company", "corp", "corporation", "france", "gmbh", "group", "groupe",
    "inc", "limited", "llc", "ltd", "plc", "sa", "sarl", "sas", "se",
}  # fmt: skip
ROLE_OVERLAP = 0.6


def words(text):
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9+#]+", plain.lower())


def company_key(name):
    """Comparable name; "Acme, Inc." and "ACME" are the same employer."""
    names = words(name)
    return " ".join(word for word in names if word not in LEGAL_WORDS) or " ".join(names)


def same_role(first, second):
    """Titles sharing most of the shorter one's words, or a title that was not given."""
    left, right = set(words(first)), set(words(second))
    if not left or not right:
        return True
    return len(left & right) / min(len(left), len(right)) >= ROLE_OVERLAP


def find_interest(interests, company, role, received_at):
    """The job post most recently opened before this email, for this company and role."""
    key = company_key(company)
    matches = [
        item
        for item in interests
        if key
        and "application_id" not in item
        and item["opened_at"] <= received_at
        and company_key(item.get("company")) == key
        and same_role(item["title"], role)
    ]
    return max(matches, key=lambda item: item["opened_at"], default=None)


def find_application(applications, company, role):
    """The most recently updated application this company and role belong to, if any."""
    key = company_key(company)
    matches = [
        item
        for item in applications
        if key and company_key(item["company"]) == key and same_role(item.get("role"), role)
    ]
    return max(matches, key=lambda item: item["updated_at"], default=None)
