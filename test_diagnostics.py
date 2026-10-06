import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

import desktop
import publish as p


def response(status, body):
    result = requests.Response()
    result.status_code = status
    result._content = body
    result.encoding = "utf-8"
    return result


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.api = p.API("private-session", self.session)

    def call(self):
        return self.api.call("POST", "/back/icviews/web/sendSmsCode")

    def test_network_errors_have_distinct_safe_messages(self):
        cases = [(requests.exceptions.ProxyError, "proxy"),
                 (requests.exceptions.SSLError, "tls"),
                 (requests.exceptions.ConnectTimeout, "connect_timeout"),
                 (requests.exceptions.ReadTimeout, "read_timeout"),
                 (requests.exceptions.ConnectionError, "connection")]
        for error_type, kind in cases:
            with self.subTest(kind=kind):
                self.session.reset_mock()
                self.session.request.side_effect = error_type("private-session 13812345678 123456")
                with self.assertRaises(p.APIError) as caught:
                    self.call()
                self.assertEqual(caught.exception.kind, kind)
                self.assertNotIn("private-session", str(caught.exception))
                self.assertNotIn("13812345678", json.dumps(caught.exception.diagnostic))
                self.assertEqual(self.session.request.call_count, 1)

    def test_empty_response_does_not_get_retried(self):
        self.session.request.return_value = response(200, b"")
        with self.assertRaises(p.APIError) as caught:
            self.call()
        self.assertEqual(caught.exception.kind, "empty_response")
        self.assertEqual(self.session.request.call_count, 1)

    def test_html_and_unexpected_json_are_not_success(self):
        for data, kind in [(b"<html>private-session</html>", "non_json"),
                           (b"[]", "server_rejected"), (b"null", "server_rejected")]:
            self.session.request.return_value = response(200, data)
            with self.subTest(data=data), self.assertRaises(p.APIError) as caught:
                self.call()
            self.assertEqual(caught.exception.kind, kind)
            self.assertNotIn("private-session", str(caught.exception))

    def test_server_message_and_code_cannot_leak_secrets(self):
        for code in (9999, "private-session"):
            payload = {"code": code, "msg": "验证码错误 123456 手机号 13812345678 private-session"}
            self.session.request.return_value = response(200, json.dumps(payload).encode())
            with self.assertRaises(p.APIError) as caught:
                self.call()
            output = str(caught.exception) + json.dumps(caught.exception.diagnostic)
            for secret in ("private-session", "13812345678", "123456"):
                self.assertNotIn(secret, output)
            self.assertIn("验证码错误", str(caught.exception))

    def test_original_reason_survives_redaction(self):
        payload = {"code": 9999, "msg": "短信校验失败，验证码已使用。mobile=13812345678 code=012345 token=private-session"}
        self.session.request.return_value = response(200, json.dumps(payload).encode())
        with self.assertRaises(p.APIError) as caught:
            self.api.call("POST", "/back/icviews/web/smsLogin",
                          json={"mobile": 13812345678, "code": 12345, "scene": 3})
        self.assertIn("验证码已使用", str(caught.exception))
        message = caught.exception.diagnostic["serverMessage"]
        for secret in ("13812345678", "012345", "12345", "private-session"):
            self.assertNotIn(secret, message)

    def test_response_urls_emails_controls_and_long_tokens_are_hidden(self):
        message = p.safe_server_message({"msg": "原因\n contact=user@example.com https://example.com/key?q=123456 0123456789abcdefABCDEF\u202e"})
        self.assertIn("原因", message)
        for secret in ("example.com", "0123456789abcdefABCDEF", "\n", "\u202e"):
            self.assertNotIn(secret, message)

    def test_http_errors_are_distinct(self):
        for status, kind in [(401, "login_required"), (403, "access_denied"), (429, "rate_limit"), (502, "http")]:
            self.session.request.return_value = response(status, b"{}")
            with self.subTest(status=status), self.assertRaises(p.APIError) as caught:
                self.call()
            self.assertEqual(caught.exception.kind, kind)
            self.assertIn(str(status), str(caught.exception))

    def test_connection_check_accepts_only_recognized_login_response(self):
        with patch.object(p, "API", return_value=self.api):
            self.session.request.return_value = response(401, b'{"code":60001}')
            self.assertIn("ICViews API", p.check_connection())
            self.assertEqual(self.session.request.call_args.args[0], "GET")
            self.session.request.return_value = response(401, b'<html>Unauthorized</html>')
            with self.assertRaises(p.APIError):
                p.check_connection()

    def test_gui_displays_safe_error_and_records_only_metadata(self):
        error = p.APIError("read_timeout", "结果不确定，请勿直接重发。", "POST", "/api?phone=13812345678")
        with tempfile.TemporaryDirectory() as temp, patch.object(p, "STATE", Path(temp)):
            message = desktop.describe_error(error)
            self.assertEqual(message, str(error))
            data = json.loads((Path(temp) / "last-error.json").read_text())
            self.assertEqual(data["kind"], "read_timeout")
            self.assertEqual(data["path"], "/api")
            self.assertNotIn("13812345678", json.dumps(data))
            unexpected = desktop.describe_error(RuntimeError("private-session"))
            self.assertNotIn("private-session", unexpected)
            self.assertIn("RuntimeError", unexpected)

    def test_gui_error_is_visible_in_status_log_and_dialog(self):
        root = desktop.tk.Tk()
        root.withdraw()
        with patch.object(desktop.sys, "argv", ["desktop.py"]):
            window = desktop.PublisherWindow(root)
        try:
            error = "服务器限制了请求频率。 [HTTP 429]"
            window.busy = True
            window.events.put((False, error, None))
            with patch.object(desktop.messagebox, "showerror") as dialog:
                window.tick()
            self.assertFalse(window.busy)
            self.assertEqual(window.status.get(), error)
            self.assertIn(error, window.log.get("1.0", "end"))
            dialog.assert_called_once_with("未完成", error, parent=root)
        finally:
            for timer in root.tk.splitlist(root.tk.call("after", "info")):
                root.after_cancel(timer)
            root.destroy()


if __name__ == "__main__":
    unittest.main()
