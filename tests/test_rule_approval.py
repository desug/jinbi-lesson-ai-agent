import hashlib
import unittest
from app.services.rule_approval import matches_approval


class ApprovalVerificationTests(unittest.TestCase):
    def test_hash_version_and_demo_binding(self):
        digest = hashlib.sha256(b'fictional').hexdigest()
        record = {'action': 'approved', 'document_id': 'A', 'version': '1', 'sha256': digest}
        doc = {'document_id': 'A', 'version': '1', 'demo': False}
        self.assertTrue(matches_approval(record, doc, digest))
        self.assertFalse(matches_approval(record, doc, hashlib.sha256(b'fictionaL').hexdigest()))
        self.assertFalse(matches_approval(record, dict(doc, version='2'), digest))
        self.assertFalse(matches_approval(record, dict(doc, demo=True), digest))
