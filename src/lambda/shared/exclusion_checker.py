EXCLUSION_TAG_KEY = "skip-enforcement"
EXCLUSION_TAG_VALUE = "true"


def has_exclusion_tag(tags: list[dict]) -> bool:
    """
    Kiem tra resource co tag Skip-Enforcement: true khong theo REQ-9.

    Yeu cau:
    - REQ-9.4: key match case-insensitive
    - Value "true" cung nen match case-insensitive de tranh sai khac True/TRUE
    - REQ-9.5: neu co nhieu tags, chi can mot tag hop le la return True
    - Mo rong: Tolerate leading/trailing whitespace o ca key va value.
    """
    if not tags:
        return False

    for tag in tags:
        key = tag.get("Key", "")
        value = tag.get("Value", "")

        normalized_key = str(key).strip().lower()
        normalized_value = str(value).strip().lower()

        if normalized_key == EXCLUSION_TAG_KEY and normalized_value == EXCLUSION_TAG_VALUE:
            return True

    return False
