"""Django/DRF integration: upload validation, authentication, ownership and file handling."""

import os
from datetime import timedelta

import django
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_test_settings")
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import TestCase  # noqa: E402
from django.test.utils import setup_test_environment, teardown_test_environment  # noqa: E402
from django.utils import timezone  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

import helpers  # noqa: E402
from bank_statement_django import conf, services  # noqa: E402
from bank_statement_django.models import StatementAnalysis  # noqa: E402

URL = "/api/bank-statement-analysis/analyze/"


@pytest.fixture(scope="module", autouse=True)
def django_db():
    from django.test.runner import DiscoverRunner

    setup_test_environment()
    runner = DiscoverRunner(verbosity=0, interactive=False)
    old = runner.setup_databases()
    yield
    runner.teardown_databases(old)
    teardown_test_environment()


PDF = helpers.build_statement_pdf()


def upload(name="statement.pdf", data=PDF, content_type="application/pdf"):
    return SimpleUploadedFile(name, data, content_type=content_type)


class StatementApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.alice = User.objects.create_user("alice", password="pw-alice-123")
        self.bob = User.objects.create_user("bob", password="pw-bob-123")
        self.client = APIClient()

    def login(self, user):
        self.client.force_authenticate(user)

    def test_requires_authentication(self):
        response = self.client.post(URL, {"statement": upload()}, format="multipart")
        self.assertIn(response.status_code, (401, 403))

    def test_successful_upload(self):
        self.login(self.alice)
        response = self.client.post(URL, {"statement": upload(), "holder_name": helpers.HOLDER}, format="multipart")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["reconciliation_status"], "VERIFIED")
        self.assertEqual(body["summary"]["total_credits"], "204422.45")
        self.assertEqual(body["summary"]["total_debits"], "106614.06")
        self.assertEqual(body["summary"]["net_cash_flow"], "97808.39")
        self.assertEqual(body["summary"]["transaction_count"], len(helpers.KNOWN_ROWS))
        self.assertTrue(body["validation"]["reconciled"])
        self.assertEqual(body["report_url"], f"/api/bank-statement-analysis/reports/{body['report_id']}/")
        self.assertNotIn("transactions", body)
        # No server paths in the response.
        self.assertNotIn(str(conf.storage_dir()), response.content.decode())

        record = StatementAnalysis.objects.get(report_id=body["report_id"])
        self.assertEqual(record.owner, self.alice)
        self.assertNotIn("transactions", record.result)  # STORE_TRANSACTIONS=False
        path = services.safe_report_path(record.report_file)
        self.assertTrue(path.is_file())
        self.assertNotIn("statement", path.name)  # generated name, not the upload name
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_rejects_non_pdf(self):
        self.login(self.alice)
        r = self.client.post(URL, {"statement": upload("notes.txt", b"hello", "text/plain")}, format="multipart")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "INVALID_PDF")
        r = self.client.post(URL, {"statement": upload("fake.pdf", b"MZ not a pdf at all")}, format="multipart")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "INVALID_PDF")

    def test_rejects_missing_field_and_oversize(self):
        self.login(self.alice)
        r = self.client.post(URL, {}, format="multipart")
        self.assertEqual(r.status_code, 400)
        big = b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024 + 10)
        r = self.client.post(URL, {"statement": upload(data=big)}, format="multipart")
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.json()["error"]["code"], "FILE_TOO_LARGE")

    def test_unrecognised_statement_returns_safe_error(self):
        self.login(self.alice)
        r = self.client.post(URL, {"statement": upload(data=helpers.text_pdf(["Hello", "World"]))}, format="multipart")
        self.assertEqual(r.status_code, 422)
        body = r.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error"]["code"], "UNSUPPORTED_STATEMENT_FORMAT")
        self.assertNotIn("Traceback", r.content.decode())
        self.assertEqual(StatementAnalysis.objects.count(), 0)

    def test_report_access_is_restricted_to_owner(self):
        self.login(self.alice)
        report_id = self.client.post(URL, {"statement": upload()}, format="multipart").json()["report_id"]
        report_url = f"/api/bank-statement-analysis/reports/{report_id}/"
        detail_url = f"/api/bank-statement-analysis/analyses/{report_id}/"

        r = self.client.get(report_url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertTrue(b"".join(r.streaming_content).startswith(b"%PDF-"))
        self.assertEqual(self.client.get(detail_url).json()["result"]["summary"]["total_credits"], "204422.45")

        self.login(self.bob)
        self.assertEqual(self.client.get(report_url).status_code, 404)
        self.assertEqual(self.client.get(detail_url).status_code, 404)
        self.assertEqual(self.client.get("/api/bank-statement-analysis/analyses/").json()["results"], [])

        self.client.force_authenticate(None)
        self.assertIn(self.client.get(report_url).status_code, (401, 403))

    def test_delete_and_retention_cleanup(self):
        self.login(self.alice)
        rid1 = self.client.post(URL, {"statement": upload()}, format="multipart").json()["report_id"]
        rid2 = self.client.post(URL, {"statement": upload()}, format="multipart").json()["report_id"]
        r1, r2 = StatementAnalysis.objects.get(report_id=rid1), StatementAnalysis.objects.get(report_id=rid2)
        p1, p2 = services.safe_report_path(r1.report_file), services.safe_report_path(r2.report_file)

        self.assertEqual(self.client.delete(f"/api/bank-statement-analysis/analyses/{rid1}/").status_code, 204)
        self.assertFalse(p1.exists())

        StatementAnalysis.objects.filter(pk=r2.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.client.get(f"/api/bank-statement-analysis/reports/{rid2}/").status_code, 404)
        call_command("purge_bank_statement_reports", verbosity=0)
        self.assertFalse(p2.exists())
        self.assertEqual(StatementAnalysis.objects.count(), 0)

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(PermissionError):
            services.safe_report_path("../../etc/passwd")
        with self.assertRaises(PermissionError):
            services.safe_report_path("C:/Windows/win.ini" if os.name == "nt" else "/etc/passwd")
        record = StatementAnalysis.objects.create(report_id="BSA-TRAVERSAL", owner=self.alice,
                                                  status="completed", report_file="../../secret.pdf")
        self.login(self.alice)
        self.assertEqual(self.client.get(f"/api/bank-statement-analysis/reports/{record.report_id}/").status_code, 404)

    def test_large_upload_temp_file_is_removed(self):
        """Uploads above FILE_UPLOAD_MAX_MEMORY_SIZE are spooled to disk by Django; nothing may remain."""
        import tempfile
        from pathlib import Path

        from django.test import override_settings

        tmp = Path(tempfile.mkdtemp(prefix="bsa-upload-"))
        self.login(self.alice)
        with override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=1024, FILE_UPLOAD_TEMP_DIR=str(tmp)):
            r = self.client.post(URL, {"statement": upload()}, format="multipart")
            self.assertEqual(r.status_code, 201)
            r = self.client.post(URL, {"statement": upload(data=helpers.text_pdf(["x"]) + b" " * 4096)},
                                 format="multipart")
            self.assertEqual(r.status_code, 422)
        self.assertEqual(list(tmp.iterdir()), [], "temporary upload files left behind")
        self.assertEqual(list(conf.storage_dir().rglob("*.tmp")), [])

    def test_logs_contain_no_statement_data(self):
        import pymupdf
        self.login(self.alice)
        doc = pymupdf.open(stream=PDF, filetype="pdf")
        locked = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="Secret#123", owner_pw="o")
        with self.assertLogs(level="DEBUG") as captured:
            self.client.post(URL, {"statement": upload(), "holder_name": helpers.HOLDER}, format="multipart")
            self.client.post(URL, {"statement": upload(data=locked), "password": "Secret#123"}, format="multipart")
            self.client.post(URL, {"statement": upload("x.txt", b"hello", "text/plain")}, format="multipart")
        text = "\n".join(captured.output)
        for secret in (helpers.HOLDER, "501000123454321", "4321", "SURESH NAIR", "ACME TECHNOLOGIES",
                       "BAJAJ", "Secret#123", "50,000.00", "107808.39"):
            self.assertNotIn(secret, text)
        self.assertIn("report=BSA-", text)  # ids/status are logged

    def test_password_protected_statement(self):
        import pymupdf
        doc = pymupdf.open(stream=PDF, filetype="pdf")
        locked = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw123", owner_pw="o")
        self.login(self.alice)
        r = self.client.post(URL, {"statement": upload(data=locked)}, format="multipart")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "ENCRYPTED_PDF")
        r = self.client.post(URL, {"statement": upload(data=locked), "password": "pw123"}, format="multipart")
        self.assertEqual(r.status_code, 201)
        self.assertNotIn("pw123", r.content.decode())
