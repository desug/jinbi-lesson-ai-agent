"""Verification primitive; caller must supply records from a trusted store.

This is not an approval workflow and does not authenticate an approver.
"""
import hmac


def matches_approval(record, document, digest):
    if record is None or document.get('demo') is not False:
        return False
    expected = record.get('sha256')
    return (record.get('action') == 'approved'
            and record.get('document_id') == document.get('document_id')
            and record.get('version') == document.get('version')
            and isinstance(expected, str) and len(expected) == 64
            and hmac.compare_digest(expected, digest))
