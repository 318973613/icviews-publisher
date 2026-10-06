"""Small native publishing desk; all network jobs stay off the UI thread."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app_runtime import APP_NAME, ROOT, VERSION, documentation_path, worker_command
import publish as publisher

HERE = ROOT
COLORS = {"paper": "#ffffff", "ink": "#20262b", "muted": "#68727b",
          "line": "#dce2e5", "surface": "#f4f6f7", "green": "#14765a", "amber": "#996319"}


def invoke_auth(operation, payload):
    try:
        result = subprocess.run(worker_command("auth", operation), input=json.dumps(payload),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                encoding="utf-8", errors="replace",
                                env=dict(os.environ, PYTHONUTF8="1"), timeout=115,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise publisher.PublishError("登录处理超时，结果不确定。请先检查短信或上次结果，不要立即重复提交。") from None
    try:
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        raise publisher.PublishError("登录进程未返回有效结果，未确认操作成功。") from None
    if result.returncode != 0 or data.get("ok") is not True:
        message = data.get("message")
        raise publisher.PublishError(message if isinstance(message, str) else "登录未成功，未保存新凭据。")
    return data.get("result")


def describe_error(error):
    publisher.record_error(error)
    if isinstance(error, publisher.PublishError):
        return str(error)
    if isinstance(error, OSError):
        return "本机文件或进程操作失败，请检查工具目录是否完整以及安全软件的拦截记录。"
    return f"程序内部错误（{type(error).__name__}），未确认操作成功。"


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.popup = widget, text, None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def show(self, _event):
        self.hide()
        self.popup = tk.Toplevel(self.widget)
        self.popup.overrideredirect(True)
        self.popup.geometry(f"+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty() + self.widget.winfo_height() + 5}")
        tk.Label(self.popup, text=self.text, bg=COLORS["ink"], fg="white", padx=9, pady=5).pack()

    def hide(self, _event=None):
        if self.popup:
            self.popup.destroy()
            self.popup = None


class PublisherWindow:
    def __init__(self, root, article_path=None):
        self.root, self.busy = root, False
        self.cooldown, self.last_login_attempt = 0, None
        self.events = queue.Queue()
        self.preview_ready = False
        self.images = {}
        root.title(f"{APP_NAME} {VERSION}")
        root.geometry("900x660")
        root.minsize(820, 620)
        root.configure(bg=COLORS["paper"])
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.configure_style()
        for name in ("app", "folder-open", "eye", "send", "activity", "log-out", "file-check", "circle-help"):
            path = ROOT / "assets" / (name + ".png")
            if path.exists():
                self.images[name] = tk.PhotoImage(file=str(path))
        if "app" in self.images:
            root.iconphoto(True, self.images["app"])

        header = ttk.Frame(root, padding=(24, 18, 24, 16))
        header.grid(row=0, column=0, sticky="ew")
        if "app" in self.images:
            ttk.Label(header, image=self.images["app"]).pack(side="left", padx=(0, 12))
        title = ttk.Frame(header)
        title.pack(side="left")
        ttk.Label(title, text="文章发布", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title, text=APP_NAME, style="Muted.TLabel").pack(anchor="w")
        ttk.Label(header, text=f"v{VERSION}  /  非官方工具", style="Muted.TLabel").pack(side="right")
        ttk.Separator(root).grid(row=1, column=0, sticky="ew")

        workspace = ttk.Frame(root, padding=(24, 20, 24, 16))
        workspace.grid(row=2, column=0, sticky="ew")
        workspace.columnconfigure(0, weight=1)
        left = ttk.Frame(workspace)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 24))
        left.columnconfigure(0, weight=1)
        ttk.Label(left, text="文章", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))
        self.article = tk.StringVar(value=str(article_path or ""))
        self.path_entry = ttk.Entry(left, textvariable=self.article)
        self.path_entry.grid(row=1, column=0, sticky="ew")
        self.path_entry.bind("<Return>", lambda _: self.inspect_article())
        self.path_entry.bind("<FocusOut>", lambda _: self.inspect_article())
        self.choose = self.button(left, "选择文章", self.choose_file, "folder-open", icon_only=True)
        self.choose.grid(row=1, column=1, padx=(8, 0))
        ttk.Label(left, text="发布标题", style="Muted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(18, 6))
        self.post_title = tk.StringVar()
        self.title_entry = ttk.Entry(left, textvariable=self.post_title)
        self.title_entry.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.summary = tk.StringVar(value="尚未选择文章")
        self.summary_label = ttk.Label(left, textvariable=self.summary, style="Muted.TLabel", wraplength=410)
        self.summary_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 16))
        actions = ttk.Frame(left)
        actions.grid(row=5, column=0, columnspan=2, sticky="w")
        self.check = self.button(actions, "检查文章", lambda: self.execute("check"), "file-check")
        self.check.pack(side="left", padx=(0, 8))
        self.preview = self.button(actions, "预览", self.preview_article, "eye")
        self.preview.pack(side="left")

        ttk.Separator(workspace, orient="vertical").grid(row=0, column=1, sticky="ns")
        right = ttk.Frame(workspace, padding=(24, 0, 0, 0))
        right.grid(row=0, column=2, sticky="nsew")
        right.columnconfigure(0, weight=1)
        ttk.Label(right, text="账号", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.account = tk.StringVar()
        self.account_label = ttk.Label(right, textvariable=self.account, style="Account.TLabel")
        self.account_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 12))
        ttk.Label(right, text="手机号", style="Muted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 5))
        self.phone = ttk.Entry(right, width=19)
        self.phone.grid(row=3, column=0, sticky="ew")
        self.send = ttk.Button(right, text="获取验证码", width=12, command=self.send_code)
        self.send.grid(row=3, column=1, padx=(8, 0))
        ttk.Label(right, text="短信验证码", style="Muted.TLabel").grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 5))
        self.code = ttk.Entry(right, show="*", width=19)
        self.code.grid(row=5, column=0, sticky="ew")
        self.login = ttk.Button(right, text="登录并保存", width=12, command=self.login_account)
        self.login.grid(row=5, column=1, padx=(8, 0))
        self.consent = tk.BooleanVar(value=False)
        self.consent_box = ttk.Checkbutton(right, variable=self.consent, text="已有账号，在本机加密保存登录状态")
        self.consent_box.grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 0))

        activity = ttk.Frame(root, padding=(24, 0, 24, 0))
        activity.grid(row=3, column=0, sticky="nsew")
        toolbar = ttk.Frame(activity)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Label(toolbar, text="运行记录", style="Section.TLabel").pack(side="left")
        self.connection = self.button(toolbar, "检测连接", self.check_connection, "activity", icon_only=True)
        self.connection.pack(side="right")
        log_frame = ttk.Frame(activity)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, height=7, wrap="word", state="disabled", relief="flat",
                           bg=COLORS["surface"], fg=COLORS["ink"], padx=12, pady=10,
                           font=("Microsoft YaHei UI", 9), borderwidth=0, highlightthickness=1,
                           highlightbackground=COLORS["line"])
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="就绪")
        self.status_label = ttk.Label(activity, textvariable=self.status, style="Muted.TLabel", wraplength=790)
        self.status_label.pack(side="bottom", fill="x", pady=(8, 6), before=log_frame)
        progress_track = ttk.Frame(root, height=3)
        progress_track.grid(row=4, column=0, sticky="ew", padx=24, pady=(0, 10))
        self.progress = ttk.Progressbar(progress_track, mode="determinate", style="Green.Horizontal.TProgressbar")
        self.progress.place(x=0, y=0, relwidth=1, relheight=1)
        ttk.Separator(root).grid(row=5, column=0, sticky="ew")
        footer = ttk.Frame(root, padding=(24, 12, 24, 16))
        footer.grid(row=6, column=0, sticky="ew")
        self.help = self.button(footer, "使用说明", self.open_help, "circle-help", icon_only=True)
        self.help.pack(side="left")
        self.logout = self.button(footer, "退出登录", self.clear_account, "log-out", icon_only=True)
        self.logout.pack(side="left", padx=(8, 0))
        self.post = self.button(footer, "发布文章", lambda: self.execute("publish"), "send", primary=True)
        self.post.pack(side="right")
        self.draft = ttk.Button(footer, text="保存草稿", command=lambda: self.execute("draft"))
        self.draft.pack(side="right", padx=(0, 10))

        self.controls = [self.path_entry, self.title_entry, self.choose, self.phone, self.code,
                         self.login, self.consent_box, self.check, self.preview, self.draft,
                         self.post, self.connection, self.logout]
        self.loaded_path = None
        self.refresh_account()
        self.inspect_article()
        root.after(100, self.tick)

    def configure_style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10), background=COLORS["paper"], foreground=COLORS["ink"])
        style.configure("TFrame", background=COLORS["paper"])
        style.configure("TLabel", background=COLORS["paper"])
        style.configure("Muted.TLabel", foreground=COLORS["muted"], font=("Microsoft YaHei UI", 9))
        style.configure("Account.TLabel", foreground=COLORS["amber"], font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 19, "bold"))
        style.configure("Section.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("TEntry", padding=7, fieldbackground="white", bordercolor=COLORS["line"], lightcolor="white", darkcolor="white")
        style.map("TEntry", bordercolor=[("focus", COLORS["green"])])
        style.configure("TButton", padding=(12, 7), background="white", bordercolor=COLORS["line"], relief="flat", focusthickness=0)
        style.map("TButton", background=[("active", "#edf3f0"), ("disabled", "#f5f6f7")], foreground=[("disabled", "#9aa2a9")])
        style.configure("Primary.TButton", background=COLORS["green"], foreground="white", bordercolor=COLORS["green"], padding=(18, 8))
        style.map("Primary.TButton", background=[("active", "#0e6149"), ("disabled", "#afc9c0")], foreground=[("disabled", "white")])
        style.configure("TCheckbutton", font=("Microsoft YaHei UI", 9), background="white")
        style.configure("Green.Horizontal.TProgressbar", background=COLORS["green"], troughcolor=COLORS["surface"],
                        bordercolor=COLORS["surface"], lightcolor=COLORS["green"], darkcolor=COLORS["green"], borderwidth=0)

    def button(self, parent, text, command, icon, icon_only=False, primary=False):
        image = self.images.get(icon + "-white") if primary else self.images.get(icon)
        if primary and image is None:
            path = ROOT / "assets" / (icon + "-white.png")
            if path.exists():
                image = self.images[icon + "-white"] = tk.PhotoImage(file=str(path))
        kwargs = {"text": "" if icon_only and image else text, "command": command,
                  "style": "Primary.TButton" if primary else "TButton"}
        if image:
            kwargs.update(image=image, compound="left")
        if icon_only:
            kwargs["width"] = 3
        button = ttk.Button(parent, **kwargs)
        if icon_only:
            Tooltip(button, text)
        return button

    def refresh_account(self):
        exists = (publisher.STATE / "credentials.json").exists()
        self.account.set("本机已有登录信息" if exists else "未登录")
        self.logout.configure(state="normal" if exists else "disabled")

    def choose_file(self):
        value = filedialog.askopenfilename(parent=self.root, filetypes=[("Markdown 文章", "*.md"), ("所有文件", "*.*")])
        if value:
            self.article.set(value)
            self.loaded_path = None
            self.inspect_article()

    def inspect_article(self):
        value = self.article.get().strip()
        if value == self.loaded_path:
            return
        self.loaded_path, self.preview_ready = value, False
        if not value:
            self.summary.set("尚未选择文章")
            self.post_title.set("")
            return
        try:
            article = publisher.Article(Path(value))
            self.post_title.set(article.title)
            self.summary.set(f"{len(article.raw):,} 字符  ·  {len(article.images)} 张本地图片")
        except (OSError, ValueError, publisher.PublishError) as error:
            self.post_title.set("")
            self.summary.set("待检查")
            self.show_log(str(error))

    def start(self, label, action, success):
        if self.busy:
            return
        self.busy = True
        self.status.set(label)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        for widget in self.controls + [self.send]:
            widget.configure(state="disabled")
        def work():
            try:
                self.events.put((True, action(), success))
            except Exception as error:
                self.events.put((False, describe_error(error), None))
        threading.Thread(target=work, daemon=True).start()

    def check_connection(self):
        self.start("正在检测连接…", publisher.check_connection, self.status.set)

    def send_code(self):
        if self.busy or self.cooldown > time.monotonic():
            return
        mobile = self.phone.get().strip()
        if not self.consent.get() or not publisher.valid_mobile(mobile):
            messagebox.showwarning("登录", "请填写已有账号的手机号，并勾选本机保存登录状态。", parent=self.root)
            return
        self.cooldown = time.monotonic() + 60
        def done(_):
            self.code.delete(0, "end")
            self.last_login_attempt = None
            self.status.set("验证码已发送，请填写手机最新收到的 6 位验证码。")
        self.start("正在请求短信验证码…", lambda: invoke_auth("sms", {"mobile": mobile}), done)

    def login_account(self):
        if self.busy:
            return
        mobile, code = self.phone.get().strip(), self.code.get().strip()
        if not self.consent.get() or not publisher.valid_mobile(mobile) or not (len(code) == 6 and code.isascii() and code.isdecimal()):
            messagebox.showwarning("登录", "请填写手机号、6 位验证码，并勾选本机保存登录状态。", parent=self.root)
            return
        attempt = publisher.digest((mobile + ":" + code).encode())
        if attempt == self.last_login_attempt:
            messagebox.showwarning("登录", "这条验证码已提交过，请先检查上一次结果，不要重复提交。", parent=self.root)
            return
        self.last_login_attempt = attempt
        def done(_):
            self.phone.delete(0, "end")
            self.code.delete(0, "end")
            self.refresh_account()
            self.account.set("已登录")
            self.status.set("登录信息已在本机加密保存。")
        self.start("正在登录…", lambda: invoke_auth("login", {"mobile": mobile, "code": code}), done)

    def run_worker(self, arguments):
        output = []
        with subprocess.Popen(worker_command("publish", *arguments), stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                              env=dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1"),
                              creationflags=subprocess.CREATE_NO_WINDOW) as process:
            for line in process.stdout:
                output.append(line)
                self.events.put(("log", line, None))
            return subprocess.CompletedProcess(process.args, process.wait(), "".join(output))

    def execute(self, command, open_preview=False):
        article = self.article.get().strip()
        if not Path(article).is_file():
            messagebox.showwarning("文章", "请先选择 Markdown 文章。", parent=self.root)
            return
        if command != "check" and not (publisher.STATE / "credentials.json").exists():
            messagebox.showwarning("登录", "请先登录自己的 ICViews 账号。", parent=self.root)
            return
        if command == "publish" and not messagebox.askyesno("确认公开发布", f"将这篇文章及其中的图片发布到 ICViews：\n\n{self.post_title.get() or Path(article).name}\n\n确定公开发布吗？", parent=self.root):
            return
        args = [command, article]
        if self.post_title.get().strip():
            args += ["--title", self.post_title.get().strip()]
        if command == "publish":
            args.append("--yes")
        self.show_log("")
        def done(result):
            if result.returncode == 0:
                self.status.set({"check": "检查通过", "draft": "草稿已保存", "publish": "服务器已确认发布成功"}[command])
                self.preview_ready = command == "check"
                if open_preview:
                    os.startfile(publisher.STATE / "preview.html")
            else:
                self.status.set("操作已停止，请查看运行记录；不要盲目重复发布。")
        self.start("正在检查文章…" if command == "check" else "正在上传图片并提交…", lambda: self.run_worker(args), done)

    def preview_article(self):
        self.execute("check", open_preview=True)

    def clear_account(self):
        if messagebox.askyesno("退出登录", "删除本机保存的登录凭据？已上传图片缓存和提交记录会保留。", parent=self.root):
            def done(result):
                self.refresh_account()
                self.status.set("本机登录凭据已移除" if result.returncode == 0 else "退出未完成，请查看运行记录。")
            self.start("正在退出登录…", lambda: self.run_worker(["logout"]), done)

    def open_help(self):
        try:
            os.startfile(documentation_path())
        except OSError:
            messagebox.showwarning("使用说明", "未找到使用说明，请确认已完整解压程序目录。", parent=self.root)

    def show_log(self, text, append=False):
        self.log.configure(state="normal")
        if not append:
            self.log.delete("1.0", "end")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def tick(self):
        while True:
            try:
                ok, value, success = self.events.get_nowait()
            except queue.Empty:
                break
            if ok == "log":
                self.show_log(value, append=True)
                continue
            self.busy = False
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            for widget in self.controls:
                widget.configure(state="normal")
            self.logout.configure(state="normal" if (publisher.STATE / "credentials.json").exists() else "disabled")
            if ok:
                try:
                    success(value)
                except Exception as error:
                    value = describe_error(error)
                    self.status.set(value)
                    self.show_log(value)
            else:
                self.status.set(value)
                self.show_log(value)
                messagebox.showerror("未完成", value, parent=self.root)
        seconds = max(0, int(self.cooldown - time.monotonic()))
        self.send.configure(text=f"{seconds} 秒后重试" if seconds else "获取验证码",
                            state="disabled" if self.busy or seconds else "normal")
        self.root.after(150, self.tick)

    def close(self):
        if self.busy:
            messagebox.showinfo("处理中", "请等待当前操作结束，以免提交结果不确定。", parent=self.root)
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    PublisherWindow(root, sys.argv[1] if len(sys.argv) == 2 else None)
    root.mainloop()


if __name__ == "__main__":
    main()
