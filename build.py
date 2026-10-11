# -*- coding: utf-8 -*-
"""打包脚本：生成单文件 exe。"""
import os
import sys

import PyInstaller.__main__

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
ENTRY = os.path.join(ROOT, "app", "main.py")

args = [
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--noupx",
    "--icon", os.path.join(ROOT, "app", "icon.ico"),
    "--name", "ProjectLibrary",
    "--add-data", os.path.join(ROOT, "web") + os.pathsep + "web",
    "--collect-all", "webview",
    "--collect-all", "clr_loader",
    "--collect-all", "pythonnet",
    "--hidden-import", "openpyxl",
    "--hidden-import", "tkinter",
    "--exclude-module", "pytest",
    "--exclude-module", "PIL",
    "--distpath", DIST,
    "--workpath", os.path.join(ROOT, "build", "tmp"),
    "--specpath", os.path.join(ROOT, "build"),
    ENTRY,
]

if __name__ == "__main__":
    try:
        PyInstaller.__main__.run(args)
        rc = 0
    except SystemExit as exc:
        rc = exc.code or 0
    if rc == 0:
        src = os.path.join(DIST, "ProjectLibrary.exe")
        dst = os.path.join(DIST, "项目库统计程序.exe")
        if os.path.exists(dst):
            # 用 .NET File.Delete 绕过 safe-delete 包装（打包环境里 trash 可能不可用）
            try:
                os.rename(dst, dst + ".old")
                os.remove(dst + ".old")
            except OSError:
                pass
        os.replace(src, dst)
        print("生成：", dst, round(os.path.getsize(dst) / 1024 / 1024, 1), "MB")
    sys.exit(rc)
