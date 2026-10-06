import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app_runtime
import desktop
import publish


class DistributionTests(unittest.TestCase):
    def test_source_worker_does_not_require_a_fixed_venv(self):
        with patch.object(app_runtime.sys, "frozen", False, create=True), \
                patch.object(app_runtime.sys, "executable", "C:/portable Python/pythonw.exe"):
            command = app_runtime.worker_command("auth", "login")
        self.assertEqual(command[0], str(Path("C:/portable Python/python.exe")))
        self.assertEqual(command[-3:], ["--worker", "auth", "login"])

    def test_frozen_worker_is_next_to_gui(self):
        with patch.object(app_runtime.sys, "frozen", True, create=True), \
                patch.object(app_runtime.sys, "executable", "C:/共享文件/portable app/ICViewsPublisher.exe"):
            command = app_runtime.worker_command("publish", "check", "article.md")
        self.assertEqual(command[0], str(Path("C:/共享文件/portable app/icviews-worker.exe")))
        self.assertNotIn(".venv", " ".join(command))
        self.assertEqual(command[-4:], ["--worker", "publish", "check", "article.md"])

    def test_new_gui_has_no_article_account_or_consent(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(publish, "STATE", Path(temp)):
            root = desktop.tk.Tk()
            root.withdraw()
            try:
                window = desktop.PublisherWindow(root)
                self.assertEqual(window.article.get(), "")
                self.assertEqual(window.post_title.get(), "")
                self.assertEqual(window.phone.get(), "")
                self.assertEqual(window.code.get(), "")
                self.assertFalse(window.consent.get())
                self.assertEqual(window.account.get(), "未登录")
                self.assertFalse(list(Path(temp).iterdir()))
            finally:
                for timer in root.tk.splitlist(root.tk.call("after", "info")):
                    root.after_cancel(timer)
                root.destroy()

    def test_example_article_is_portable(self):
        article = publish.Article(app_runtime.ROOT / "examples" / "示例文章.md")
        self.assertEqual(len(article.images), 1)
        self.assertIn("<table>", article.render({}))

    def test_gui_footer_and_inputs_fit_at_minimum_size(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(publish, "STATE", Path(temp)):
            root = desktop.tk.Tk()
            try:
                window = desktop.PublisherWindow(root)
                for size in ("900x660", "820x620"):
                    root.geometry(size)
                    root.update()
                    for widget in (window.post, window.draft, window.path_entry,
                                   window.title_entry, window.login, window.status_label, window.log):
                        self.assertGreater(widget.winfo_height(), 1)
                        self.assertGreaterEqual(widget.winfo_rootx(), root.winfo_rootx())
                        self.assertGreaterEqual(widget.winfo_rooty(), root.winfo_rooty())
                        self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                                             root.winfo_rootx() + root.winfo_width())
                        self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                             root.winfo_rooty() + root.winfo_height())
            finally:
                for timer in root.tk.splitlist(root.tk.call("after", "info")):
                    root.after_cancel(timer)
                root.destroy()


if __name__ == "__main__":
    unittest.main()
