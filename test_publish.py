import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests
import publish as p

URL = "https://icviews-1314446280.cos.ap-beijing.myqcloud.com/files/test.png"
PNG = b"\x89PNG\r\n\x1a\nfixture"


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "image.png").write_bytes(PNG)

    def article(self, body):
        source = self.root / "article.md"
        source.write_text("# Title\n\n" + body, encoding="utf-8")
        return p.Article(source)

    def test_markdown_images_and_fences(self):
        article = self.article("| A | B |\n|---|---|\n| 1 | 2 |\n\n![alt](image.png)\n\n```c\n![not image](missing.png)\n<x>\n```\n")
        self.assertEqual(len(article.images), 1)
        result = article.render({p.digest(PNG): URL})
        self.assertIn("<table>", result)
        self.assertIn('class="language-c"', result)
        self.assertIn(URL, result)
        self.assertIn("&lt;x&gt;", result)
        self.assertNotIn("<h1>", result)
        self.assertIn("image.png", article.source.read_text())

    def test_reference_style_images(self):
        article = self.article("![one][image]\n\n[image]: image.png\n")
        self.assertEqual(len(article.images), 1)

    def test_reject_missing_and_traversal(self):
        for path in ("missing.png", "../private.png", "%2e%2e/private.png"):
            with self.subTest(path=path), self.assertRaises(p.PublishError):
                self.article(f"![x]({path})")
        with self.assertRaises(p.PublishError):
            p.local_image(self.root, "file:///private.png")
        self.assertNotIn("<img", self.article("![x](file:///private.png)").render({}))

    def test_raw_html_is_not_executed(self):
        rendered = self.article('<script>alert(1)</script>').render({})
        self.assertNotIn("<script>", rendered)

    def test_title_and_body_limits(self):
        with self.assertRaises(p.PublishError):
            self.article("x" * 100001)
        with self.assertRaises(p.PublishError):
            p.Article(self.root / "article.md", "T" * 121)

    def test_headers_multipart_and_response(self):
        session = Mock()
        session.request.return_value.status_code = 200
        session.request.return_value.json.return_value = {"code": 200, "data": {"url": URL}}
        self.assertEqual(p.API("test-session", session).upload(self.root / "image.png"), URL)
        args, kwargs = session.request.call_args
        self.assertEqual(args, ("POST", p.BASE + "/back/icviews/common/file/upload/image"))
        self.assertEqual(kwargs["headers"], {"Authorization": "test-session", "third-session": "test-session"})
        self.assertEqual(kwargs["files"]["file"], ("image.png", PNG, "image/png"))
        self.assertFalse(kwargs["allow_redirects"])

    def test_login_failures_and_redirects(self):
        for status, code in ((401, 200), (302, 200), (200, 60001), (200, 500)):
            session = Mock()
            session.request.return_value.status_code = status
            session.request.return_value.json.return_value = {"code": code}
            with self.subTest(status=status, code=code), self.assertRaises(p.PublishError):
                p.API("secret", session).call("GET", "/test")

    def test_network_error_does_not_reveal_secret(self):
        session = Mock()
        session.request.side_effect = requests.Timeout("secret")
        with self.assertRaises(p.PublishError) as error:
            p.API("secret", session).call("POST", "/test")
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(session.request.call_count, 1)

    def test_resume_partial_upload(self):
        (self.root / "two.png").write_bytes(PNG + b"two")
        article = self.article("![a](image.png)\n![b](two.png)")
        cache = self.root / "cache.json"
        api = Mock()
        api.upload.side_effect = [URL, p.PublishError("offline")]
        with self.assertRaises(p.PublishError):
            p.upload_images(article, api, cache)
        self.assertEqual(len(json.loads(cache.read_text())), 1)
        api.upload.reset_mock(side_effect=True)
        api.upload.return_value = URL
        p.upload_images(article, api, cache)
        self.assertEqual(api.upload.call_count, 1)

    def test_duplicate_images_uploaded_once(self):
        article = self.article("![a](image.png)\n![b](image.png)")
        api = Mock()
        api.upload.return_value = URL
        p.upload_images(article, api, self.root / "cache.json")
        self.assertEqual(api.upload.call_count, 1)

    def test_publish_receipt_blocks_retry_even_on_lost_response(self):
        api = Mock()
        api.call.side_effect = p.PublishError("lost response")
        receipt = self.root / "receipt.json"
        payload = {"title": "T", "content": "<p>x</p>", "userId": 1, "topicIds": []}
        with self.assertRaises(p.PublishError):
            p.submit(api, "/post", payload, receipt)
        self.assertEqual(p.read_json(receipt, {})["status"], "unknown")
        with self.assertRaises(p.PublishError):
            p.submit(api, "/post", payload, receipt)
        self.assertEqual(api.call.call_count, 1)

    def test_confirmed_receipt_and_exact_payload(self):
        api = Mock()
        api.call.return_value = {"id": 123}
        receipt = self.root / "receipt.json"
        payload = {"title": "T", "content": "<p>x</p>", "userId": 1, "topicIds": [2]}
        p.submit(api, "/post", payload, receipt)
        api.call.assert_called_once_with("POST", "/post", json=payload)
        self.assertEqual(p.read_json(receipt, {})["status"], "confirmed")
        with self.assertRaises(p.PublishError):
            p.submit(api, "/post", payload, receipt)

    def run_args(self, command, **overrides):
        article = self.article("![a](image.png)")
        values = dict(command=command, article=str(article.source), title=None,
                      output=str(self.root / "preview.html"), yes=False,
                      replace_draft=False, topic=None, import_map=None)
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_check_is_offline(self):
        with patch.object(p, "credentials") as auth, patch.object(p, "API") as api:
            p.run(self.run_args("check"))
        auth.assert_not_called()
        api.assert_not_called()

    def test_publish_requires_explicit_yes(self):
        with patch.object(p, "credentials") as auth, self.assertRaises(p.PublishError):
            p.run(self.run_args("publish"))
        auth.assert_not_called()

    def test_existing_draft_not_overwritten(self):
        api = Mock()
        api.call.return_value = {"id": 123, "title": "Existing"}
        with patch.object(p, "STATE", self.root / "state"), \
                patch.object(p, "credentials", return_value=(1, "test")), \
                patch.object(p, "API", return_value=api), self.assertRaises(p.PublishError):
            p.run(self.run_args("draft"))
        api.upload.assert_not_called()
        api.call.assert_called_once_with("GET", "/back/icviews/wx/semi/draft?type=0")

    def test_publish_flow(self):
        api = Mock()
        api.call.side_effect = [{}, {"id": 321}]
        api.upload.return_value = URL
        with patch.object(p, "STATE", self.root / "state"), \
                patch.object(p, "credentials", return_value=(1, "test")), \
                patch.object(p, "API", return_value=api):
            p.run(self.run_args("publish", yes=True))
        args, kwargs = api.call.call_args
        self.assertEqual(args, ("POST", "/back/icviews/wx/semi/posts"))
        self.assertEqual(kwargs["json"]["userId"], 1)
        self.assertIn(URL, kwargs["json"]["content"])
        self.assertNotIn("file://", kwargs["json"]["content"])

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_windows_encryption(self):
        encrypted = p.protect(b"fixture-not-a-real-session")
        self.assertNotIn(b"fixture-not-a-real-session", encrypted)
        self.assertEqual(p.protect(encrypted, True), b"fixture-not-a-real-session")

    def test_sms_validation_sends_nothing(self):
        with patch.object(p, "API") as api:
            for phone in ("123", "１3812345678", "1381234567x"):
                with self.assertRaises(p.PublishError):
                    p.send_sms(phone)
            with self.assertRaises(p.PublishError):
                p.login_sms("13812345678", "123")
            api.assert_not_called()

    def test_sms_request_contract(self):
        with patch.object(p, "API") as api:
            p.send_sms("13812345678")
            api.return_value.call.assert_called_once_with("POST", "/back/icviews/web/sendSmsCode",
                                                         json={"mobile": "13812345678", "scene": 3})

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_sms_login_stores_encrypted_session(self):
        with patch.object(p, "STATE", self.root / "state"), patch.object(p, "API") as api:
            api.return_value.call.return_value = {"id": 12, "sessionKey": "fixture-session"}
            self.assertEqual(p.login_sms("13812345678", "012345"), 12)
            api.return_value.call.assert_called_once_with("POST", "/back/icviews/web/smsLogin",
                json={"mobile": 13812345678, "code": 12345, "scene": 3})
            self.assertEqual(p.credentials(), (12, "fixture-session"))
            self.assertNotIn("fixture-session", (p.STATE / "credentials.json").read_text())

    def test_sms_invalid_response_not_stored(self):
        with patch.object(p, "STATE", self.root / "state"), patch.object(p, "API") as api:
            api.return_value.call.return_value = {"id": 12}
            with self.assertRaises(p.PublishError):
                p.login_sms("13812345678", "123456")
            self.assertFalse((p.STATE / "credentials.json").exists())

    def test_login_accepts_numeric_strings_without_precision_loss(self):
        for value in ("12", " 12 ", "9007199254740993", "9223372036854775807"):
            with self.subTest(value=value):
                self.assertEqual(p.parse_login_response({"id": value, "sessionKey": "fixture"}),
                                 (int(value), "fixture"))

    def test_login_rejects_invalid_ids_and_tokens(self):
        for value in (None, True, False, 0, -1, 12.0, "", "0", "-1", "12.0", "１２", "../account", {}, [], "1" * 65):
            with self.subTest(value=value), self.assertRaises(p.APIError):
                p.parse_login_response({"id": value, "sessionKey": "fixture"})
        for token in (None, "", "  ", 123, {}, "fixture\r\n", "fixture\x00", "fixture\t", "fixture\x7f"):
            with self.subTest(token=token), self.assertRaises(p.APIError):
                p.parse_login_response({"id": "12", "sessionKey": token})

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_string_id_login_is_encrypted_and_round_trips(self):
        with patch.object(p, "STATE", self.root / "state"), patch.object(p, "API") as api:
            api.return_value.call.return_value = {"id": "9007199254740993", "sessionKey": "fixture-session"}
            self.assertEqual(p.login_sms("13812345678", "012345"), 9007199254740993)
            self.assertEqual(p.credentials(), (9007199254740993, "fixture-session"))
            self.assertNotIn("fixture-session", (p.STATE / "credentials.json").read_text())
            self.assertEqual(api.return_value.call.call_count, 1)

    def test_login_schema_diagnostics_never_contain_field_values(self):
        for result in ({"id": "bad-private-id", "sessionKey": "private-session"},
                       {"id": "12"}, None, ["private-session"]):
            with self.subTest(result=result), self.assertRaises(p.APIError) as caught:
                p.parse_login_response(result)
            diagnostic = caught.exception.diagnostic
            self.assertEqual(diagnostic["kind"], "login_response")
            self.assertIn("idType", diagnostic["schema"])
            self.assertNotIn("private", json.dumps(diagnostic) + str(caught.exception))


if __name__ == "__main__":
    unittest.main()
