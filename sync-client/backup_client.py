#!/usr/bin/env python3
"""Standalone, standard-library PKM backup downloader. Never extracts archives."""
import argparse
import getpass
import hashlib
import http.cookiejar
import json
import math
import os
from pathlib import Path
import queue
import re
import tempfile
import threading
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPRedirectHandler

DEFAULT_SERVER = "http://8.133.220.164:8091"
DEFAULT_DIRECTORY = r"D:\PKM备份"
DEFAULT_INTERVAL = 30.0
HTTP_WARNING = "警告：HTTP 不加密，密码、会话和备份可能被窃听或篡改。建议使用 HTTPS。仍继续吗？"


class BackupError(Exception):
    pass


class Cancelled(BackupError):
    pass


def normalize_server(value):
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment):
        raise BackupError("服务器必须是 http(s)://主机[:端口]，不能含路径或凭证")
    try:
        parsed.port
    except ValueError as exc:
        raise BackupError("服务器端口无效") from exc
    return value


def positive_interval(value):
    value = float(value)
    if not math.isfinite(value) or value < 0.01:
        raise ValueError("间隔至少 0.01 分钟")
    return value


def config_path():
    base = Path(os.environ.get("APPDATA") or Path.home() / ".config")
    return base / "pkm-backup" / "config.json"


def load_config(path=None):
    try:
        data = json.loads((path or config_path()).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {key: data[key] for key in ("server", "dir", "interval") if key in data}
    except (OSError, ValueError):
        return {}


def save_config(server, directory, interval, path=None):
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"server": normalize_server(server), "dir": str(directory),
            "interval": positive_interval(interval)}
    fd, tmp = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def safe_filename(name):
    if (not isinstance(name, str) or not name or len(name) > 200
            or name in (".", "..") or name[0] in " ." or name[-1] in " ."
            or not name.lower().endswith(".zip")
            or any(ord(c) < 32 or ord(c) == 127 or c in '/\\:<>\"|?*' for c in name)
            or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", name, re.I)):
        raise BackupError("服务器返回不安全的 ZIP 文件名")
    return name


def metadata(item):
    if not isinstance(item, dict):
        raise BackupError("备份记录必须是对象")
    filename = safe_filename(item.get("filename"))
    size = item.get("size")
    digest = item.get("sha256")
    identifier = item.get("id")
    if (type(size) is not int or size < 0 or not isinstance(digest, str)
            or not re.fullmatch(r"[a-fA-F0-9]{64}", digest)
            or type(identifier) not in (str, int) or not str(identifier)):
        raise BackupError("备份大小、SHA256 或 ID 无效")
    try:
        created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            raise ValueError("timezone required")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise BackupError("created_at 必须为带时区的 ISO8601 时间") from exc
    return dict(id=str(identifier), filename=filename, size=size,
                sha256=digest.lower(), created=created.astimezone(timezone.utc))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BackupError("拒绝 HTTP 重定向；请填写最终服务器地址")


class BackupClient:
    def __init__(self, server, directory, password, *, allow_http=False, timeout=15):
        self.server = normalize_server(server)
        if self.server.startswith("http:") and not allow_http:
            raise BackupError("HTTP 必须显式确认风险后才能连接")
        self.directory = Path(directory).expanduser()
        self.password = password  # Only memory, never persisted or logged.
        self.timeout = timeout
        self.cookies = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies), NoRedirect())
        self.authenticated = False

    def _open(self, path, data=None):
        headers = {"Origin": self.server, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        return self.opener.open(Request(self.server + path, data=data, headers=headers),
                                timeout=self.timeout)

    def login(self):
        self.authenticated = False
        body = json.dumps({"password": self.password}).encode("utf-8")
        with self._open("/api/auth/login", body) as response:
            response.read(1024 * 1024)
        self.authenticated = True

    def request(self, path):
        if not self.authenticated:
            self.login()
        try:
            return self._open(path)
        except HTTPError as exc:
            if exc.code != 401:
                raise
            exc.close()
            self.login()
            return self._open(path)  # One retry only; wrong password never loops.

    @staticmethod
    def _check_stop(stop):
        if stop is not None and stop.is_set():
            raise Cancelled("已停止")

    def _matches(self, path, item, stop):
        if path.is_symlink() or not path.is_file():
            raise BackupError("本地同名路径不是普通文件，拒绝覆盖")
        if path.stat().st_size != item["size"]:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                self._check_stop(stop)
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest() == item["sha256"]

    def sync_once(self, stop=None):
        self._check_stop(stop)
        with self.request("/api/backups/") as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise BackupError("备份列表过大")
        items = json.loads(raw)
        if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
            raise BackupError("备份列表必须是 JSON 数组")
        completed = [metadata(x) for x in items if x.get("status") == "completed"]
        completed.sort(key=lambda x: (x["created"], x["id"]), reverse=True)
        self.directory.mkdir(parents=True, exist_ok=True)
        for item in completed:
            self._check_stop(stop)
            target = self.directory / item["filename"]
            if target.exists() or target.is_symlink():
                if self._matches(target, item, stop):
                    continue
                raise BackupError("本地同名文件与服务器校验不符；保留原文件，请选择其他目录")
            self._download(item, target, stop)
            return target
        return None

    def _download(self, item, target, stop):
        fd, tmp = tempfile.mkstemp(prefix=".pkm-", suffix=".part", dir=self.directory)
        try:
            digest, count = hashlib.sha256(), 0
            with os.fdopen(fd, "wb") as handle:
                with self.request("/api/backups/" + quote(item["id"], safe="") + "/download") as response:
                    while True:
                        self._check_stop(stop)
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > item["size"]:
                            raise BackupError("下载大小超出服务器声明")
                        handle.write(chunk)
                        digest.update(chunk)
                if count != item["size"] or digest.hexdigest() != item["sha256"]:
                    raise BackupError("下载大小或 SHA256 校验失败")
                handle.flush()
                os.fsync(handle.fileno())
            self._check_stop(stop)
            # Atomic no-clobber publication: a concurrent existing file is never replaced.
            os.link(tmp, target)
        finally:
            os.unlink(tmp)  # Only our own temporary .part, never an existing backup.


def safe_error(exc):
    # Do not echo server bodies, URLs, credentials, or exception payloads.
    if isinstance(exc, BackupError):
        return str(exc)
    if isinstance(exc, HTTPError):
        return "HTTP 错误 %s（检查服务端和密码）" % exc.code
    return "操作失败（%s）；未标记下载成功，请检查网络、磁盘与 API" % type(exc).__name__


def run_watch(client, minutes, stop, report):
    interval = positive_interval(minutes) * 60
    while not stop.is_set():
        try:
            result = client.sync_once(stop)
            report("已校验并保存：" + str(result) if result else "没有尚未下载的 completed 备份")
        except Cancelled:
            return
        except Exception as exc:
            report(safe_error(exc))
        if stop.wait(interval):
            return


def run_gui(settings):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk()
    root.title("PKM ZIP 备份客户端（不自动解压）")
    server = tk.StringVar(value=settings["server"])
    directory = tk.StringVar(value=settings["dir"])
    interval = tk.StringVar(value=str(settings["interval"]))
    password = tk.StringVar()
    status = tk.StringVar(value="密码仅保存在当前进程；关闭窗口不会后台常驻")
    events = queue.Queue()
    stop = threading.Event()
    worker = None
    closing = False
    fields = []
    for row, (label, variable) in enumerate((("服务器", server), ("备份目录", directory),
                                             ("间隔（分钟）", interval), ("密码", password))):
        ttk.Label(root, text=label).grid(row=row, column=0, padx=8, pady=6, sticky="w")
        entry = ttk.Entry(root, textvariable=variable, width=55, show="*" if row == 3 else "")
        entry.grid(row=row, column=1, padx=8, pady=6)
        fields.append(entry)

    def choose():
        value = filedialog.askdirectory(initialdir=directory.get(), mustexist=False)
        if value:
            directory.set(value)

    browse = ttk.Button(root, text="选择目录", command=choose)
    browse.grid(row=1, column=2, padx=8)

    def start(watch):
        nonlocal worker
        if worker is not None and worker.is_alive():
            return
        try:
            url = normalize_server(server.get())
            minutes = positive_interval(interval.get())
            if not directory.get().strip() or not password.get():
                raise BackupError("请填写目录和密码")
            if url.startswith("http:") and not messagebox.askyesno("HTTP 安全警告", HTTP_WARNING):
                return
            save_config(url, directory.get(), minutes)
            client = BackupClient(url, directory.get(), password.get(), allow_http=True)
        except Exception as exc:
            messagebox.showerror("无法开始", safe_error(exc))
            return
        stop.clear()
        def work():
            try:
                if watch:
                    run_watch(client, minutes, stop, events.put)
                else:
                    result = client.sync_once(stop)
                    events.put("已校验并保存：" + str(result) if result else "没有尚未下载的 completed 备份")
            except Exception as exc:
                events.put(safe_error(exc))
            finally:
                client.password = ""
                if stop.is_set():
                    events.put("已停止；可再次立刻同步")
        worker = threading.Thread(target=work, name="pkm-backup", daemon=True)
        worker.start()
        status.set("同步中…停止会在当前网络读取结束后生效（网络超时 15 秒）")

    def halt():
        stop.set()
        status.set("正在停止；等待当前网络读取结束…")

    def close():
        nonlocal closing
        closing = True
        stop.set()
        password.set("")
        # Daemon worker cannot keep the process alive. Incomplete .part is not success.
        root.destroy()

    once = ttk.Button(root, text="立刻同步一次", command=lambda: start(False))
    once.grid(row=4, column=0, padx=8, pady=10)
    watch_button = ttk.Button(root, text="开始定时同步", command=lambda: start(True))
    watch_button.grid(row=4, column=1, sticky="w", padx=8)
    ttk.Button(root, text="停止", command=halt).grid(row=4, column=2, padx=8)
    ttk.Label(root, textvariable=status, wraplength=620).grid(row=5, column=0, columnspan=3, padx=8, pady=12)

    def tick():
        if closing:
            return
        active = worker is not None and worker.is_alive()
        for widget in fields + [browse, once, watch_button]:
            widget.configure(state="disabled" if active else "normal")
        while True:
            try:
                status.set(events.get_nowait())
            except queue.Empty:
                break
        root.after(200, tick)
    root.protocol("WM_DELETE_WINDOW", close)
    tick()
    root.mainloop()


def main(argv=None):
    saved = load_config()
    parser = argparse.ArgumentParser(description="PKM 完整 ZIP 备份客户端（Python 标准库）")
    parser.add_argument("--server", default=saved.get("server", DEFAULT_SERVER))
    parser.add_argument("--directory", default=saved.get("dir", DEFAULT_DIRECTORY))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="仅同步最新的一份未下载备份")
    mode.add_argument("--watch", type=positive_interval, metavar="分钟", help="失败也按间隔重试")
    mode.add_argument("--gui", action="store_true", help="启动目录选择图形界面（默认）")
    args = parser.parse_args(argv)
    settings = {"server": args.server, "dir": args.directory,
                "interval": args.watch if args.watch is not None else saved.get("interval", DEFAULT_INTERVAL)}
    if not args.once and args.watch is None:
        run_gui(settings)
        return 0
    client = None
    try:
        server = normalize_server(args.server)
        if server.startswith("http:"):
            if input(HTTP_WARNING + " 输入 YES 确认：").strip() != "YES":
                print("已取消；未发送密码")
                return 1
        password = getpass.getpass("PKM 密码（不保存）：")
        if not password:
            raise BackupError("密码不能为空")
        save_config(server, args.directory, settings["interval"])
        client = BackupClient(server, args.directory, password, allow_http=True)
        if args.watch is not None:
            run_watch(client, args.watch, threading.Event(), print)
        else:
            result = client.sync_once()
            print("已校验并保存：" + str(result) if result else "没有尚未下载的 completed 备份")
        return 0
    except KeyboardInterrupt:
        print("\n已停止")
        return 130
    except (Exception, EOFError) as exc:
        print(safe_error(exc))
        return 1
    finally:
        if client is not None:
            client.password = ""


if __name__ == "__main__":
    raise SystemExit(main())
