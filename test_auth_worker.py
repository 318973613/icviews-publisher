import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import auth_worker
import desktop
import publish as p


class AuthWorkerTests(unittest.TestCase):
    def test_sms_and_login_contracts(self):
        with patch.object(p, "send_sms") as sms, patch.object(p, "login_sms", return_value=12) as login:
            self.assertEqual(auth_worker.handle("sms", {"mobile": "13812345678"}), {"ok": True, "result": None})
            sms.assert_called_once_with("13812345678")
            self.assertEqual(auth_worker.handle("login", {"mobile": "13812345678", "code": "012345"}),
                             {"ok": True, "result": 12})
            login.assert_called_once_with("13812345678", "012345")

    def test_unexpected_error_does_not_reveal_inputs(self):
        with patch.object(p, "login_sms", side_effect=RuntimeError("13812345678 012345 private-session")):
            result = auth_worker.handle("login", {"mobile": "13812345678", "code": "012345"})
        self.assertFalse(result["ok"])
        self.assertNotIn("13812345678", json.dumps(result))
        self.assertNotIn("private-session", json.dumps(result))

    def test_worker_records_error_without_exposing_credentials(self):
        error = p.APIError("server_rejected", "验证码已使用", "POST", "/back/icviews/web/smsLogin", 200, 9999)
        with tempfile.TemporaryDirectory() as temp, patch.object(p, "STATE", Path(temp)), \
                patch.object(p, "login_sms", side_effect=error):
            result = auth_worker.handle("login", {"mobile": "13812345678", "code": "012345"})
            self.assertIn("验证码已使用", result["message"])
            data = json.loads((p.STATE / "last-error.json").read_text())
            self.assertEqual(data["serverCode"], 9999)
            self.assertNotIn("13812345678", json.dumps(data))

    def test_private_inputs_use_stdin_not_process_arguments(self):
        result = Mock(returncode=0, stdout='{"ok":true,"result":12}')
        with patch.object(desktop.subprocess, "run", return_value=result) as run:
            self.assertEqual(desktop.invoke_auth("login", {"mobile": "13812345678", "code": "012345"}), 12)
        command = run.call_args.args[0]
        self.assertNotIn("13812345678", " ".join(command))
        self.assertNotIn("012345", " ".join(command))
        self.assertEqual(json.loads(run.call_args.kwargs["input"])["code"], "012345")

    def test_worker_error_reaches_gui(self):
        result = Mock(returncode=1, stdout=json.dumps({"ok": False, "message": "验证码已使用"}))
        with patch.object(desktop.subprocess, "run", return_value=result), self.assertRaises(p.PublishError) as caught:
            desktop.invoke_auth("login", {"mobile": "13812345678", "code": "012345"})
        self.assertIn("验证码已使用", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
