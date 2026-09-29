"""Canonical comparison keys for ISO names and EP3D filename escapes."""
import re


def sheet_id_key(value: str) -> str:
    # EP3D exports slash as ^2F, not a literal caret. Decode only the
    # observed separator escapes; do not conflate unrelated drawing names.
    value = re.sub(r'\^2f', '/', str(value).strip(), flags=re.I)
    value = re.sub(r'\^5c', '/', value, flags=re.I)
    return value.replace('\\', '/').casefold()
