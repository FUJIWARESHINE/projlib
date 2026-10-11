# -*- coding: utf-8 -*-
"""项目库统计程序 · 桌面入口。"""
from __future__ import annotations

import os
import socket
import sys
import traceback
import webbrowser

import db
import server

APP_TITLE = "项目库统计程序"
LOCK_FILE = os.path.join(db.get_data_dir(), "port.lock")


def _log_error(msg: str) -> None:
    try:
        with open(os.path.join(db.get_data_dir(), "error.log"), "a", encoding="utf-8") as f:
            import time
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "  " + msg + "\n")
    except Exception:
        pass


def _write_lock(port: int) -> None:
    try:
        with open(LOCK_FILE, "w", encoding="utf-8") as f:
            f.write(str(port))
    except Exception:
        pass


def _running_port():
    if not os.path.exists(LOCK_FILE):
        return None
    try:
        with open(LOCK_FILE, "r", encoding="utf-8") as f:
            port = int(f.read().strip())
    except Exception:
        return None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            return port
    return None


def _open_browser(port: int) -> None:
    url = f"http://127.0.0.1:{port}/index.html"
    for browser in ("edge", "chrome"):
        try:
            webbrowser.get(browser).open(url)
            return
        except Exception:
            continue
    webbrowser.open(url)


def _fallback_window(port: int) -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("360x180")
    root.resizable(False, False)
    tk.Label(root, text=APP_TITLE, font=("微软雅黑", 14, "bold")).pack(pady=(20, 6))
    tk.Label(root, text="已在浏览器中打开，请勿关闭本窗口", font=("微软雅黑", 10)).pack()
    tk.Button(root, text="重新打开界面", width=18,
              command=lambda: _open_browser(port)).pack(pady=(14, 4))
    tk.Button(root, text="退出程序", width=18, command=root.destroy).pack()
    root.mainloop()
    os._exit(0)


def main() -> None:
    try:
        running = _running_port()
        if running:
            _open_browser(running)
            return
        httpd, port = server.start_server()
        _write_lock(port)
        try:
            import webview
            webview.create_window(
                APP_TITLE,
                f"http://127.0.0.1:{port}/index.html",
                width=1460, height=920, min_size=(1024, 640), text_select=True,
            )
            webview.start(debug=False)
        except Exception:  # noqa: BLE001
            _log_error("webview 启动失败：" + traceback.format_exc())
            _open_browser(port)
            _fallback_window(port)
    except Exception:  # noqa: BLE001
        _log_error(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
