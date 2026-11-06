from datetime import date, datetime, timezone
from io import BytesIO
import unittest
from app.scanner.ecb_session_fx import ECBSessionFXProvider

NOW = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
D = date(2026, 9, 21)
XML = b'<Envelope xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref"><Cube><Cube time="2026-09-21"><Cube currency="USD" rate="1.25"/><Cube currency="GBP" rate="0.8"/></Cube></Cube></Envelope>'

class FXTests(unittest.TestCase):
    def provider(self, payload=XML):
        return ECBSessionFXProvider(opener=lambda *a, **kw: BytesIO(payload), now=lambda: NOW)

    def test_inversion_provenance_and_observed_timestamp(self):
        r = self.provider().fetch("USD", sessions=[D])
        self.assertEqual(r.status, "SUCCESS")
        self.assertEqual(r.rates[0].eur_per_unit, .8)
        self.assertEqual(r.rates[0].known_at, NOW)
        self.assertIn(r.response_sha256, r.rates[0].source)
        self.assertEqual(r.raw_xml.encode(), XML)

    def test_gbp_major_currency_not_pence(self):
        self.assertEqual(self.provider().fetch("GBP", sessions=[D]).rates[0].eur_per_unit, 1.25)
        with self.assertRaises(ValueError):
            self.provider().fetch("GBX", sessions=[D])

    def test_holiday_old_date_and_unknown_currency_not_filled(self):
        r = self.provider().fetch("USD", sessions=[date(2025, 1, 1), date(2026, 9, 20), D])
        self.assertEqual(r.status, "PARTIAL")
        self.assertEqual(len(r.rates), 1)
        self.assertEqual(len(r.missing_sessions), 2)
        self.assertFalse(self.provider().fetch("ZZZ", sessions=[D]).rates)

    def test_invalid_payloads_fail_closed(self):
        for payload in (b"<html/>", XML.replace(b'1.25', b'0'), XML.replace(b'1.25', b'nan'),
                        XML.replace(b'1.25', b'-1'), XML.replace(b'1.25', b'inf'),
                        XML.replace(b'currency="GBP"', b'currency="USD"'),
                        XML.replace(b'2026-09-21', b'2026-09-23'),
                        b'<!DOCTYPE a>'+XML, b'x'*2_000_001,
                        XML.replace(b'</Cube></Envelope>', b'<Cube time="2026-09-21"/></Cube></Envelope>')):
            with self.subTest(payload=payload[:40]):
                r = self.provider(payload).fetch("USD", sessions=[D])
                self.assertEqual(r.status, "ERROR")
                self.assertFalse(r.rates)

    def test_request_validation(self):
        for sessions in ([], [D, D], [NOW]):
            with self.assertRaises(ValueError):
                self.provider().fetch("USD", sessions=sessions)

    def test_transport_error_no_payload_leak(self):
        def broken(*a, **kw):
            raise TimeoutError("private-token")
        r = ECBSessionFXProvider(opener=broken, now=lambda: NOW).fetch("USD", sessions=[D])
        self.assertEqual(r.status, "ERROR")
        self.assertNotIn("private-token", repr(r))
        self.assertFalse(r.rates)
