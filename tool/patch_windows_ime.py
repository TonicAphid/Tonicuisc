"""Patch the generated Windows runner for the Flutter IME focus bug.

Flutter engine issue: https://github.com/flutter/flutter/issues/190042
After the window loses and regains focus, the IMM input context can go stale,
so third-party IMEs (Sogou / WeType / ...) stop producing composition messages.
Restoring the window's default input context is the workaround used in production.

Run after `flutter create --platforms=windows`:
    python tool/patch_windows_ime.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CPP = ROOT / "app" / "windows" / "runner" / "flutter_window.cpp"
CMAKE = ROOT / "app" / "windows" / "runner" / "CMakeLists.txt"

INCLUDE_ANCHOR = '#include "flutter/generated_plugin_registrant.h"'
HANDLER_ANCHOR = "  // Give Flutter, including plugins, an opportunity to handle window messages."

PATCH = """  // Windows IME input context can become stale after the window loses and
  // regains focus, which breaks third-party IMEs (flutter/flutter#190042).
  // Restore the window's default IMM input context on focus/activation.
  if (message == WM_SETFOCUS ||
      (message == WM_ACTIVATE && wparam != WA_INACTIVE)) {
    if (flutter_controller_ && flutter_controller_->view()) {
      ::ImmAssociateContextEx(
          flutter_controller_->view()->GetNativeWindow(), nullptr, IACE_DEFAULT);
    }
  }

"""


def patch_cpp() -> None:
    text = CPP.read_text(encoding="utf-8")
    if "ImmAssociateContextEx" in text:
        print("flutter_window.cpp: already patched")
        return
    if INCLUDE_ANCHOR not in text:
        raise SystemExit(f"找不到 include 锚点: {CPP}")
    if HANDLER_ANCHOR not in text:
        raise SystemExit(f"找不到 MessageHandler 锚点: {CPP}")

    text = text.replace(INCLUDE_ANCHOR, INCLUDE_ANCHOR + "\n\n#include <imm.h>", 1)
    text = text.replace(HANDLER_ANCHOR, PATCH + HANDLER_ANCHOR, 1)
    CPP.write_text(text, encoding="utf-8")
    print("flutter_window.cpp: patched")


def patch_cmake() -> None:
    text = CMAKE.read_text(encoding="utf-8")
    if "imm32" in text:
        print("CMakeLists.txt: already patched")
        return
    anchor = 'target_link_libraries(${BINARY_NAME} PRIVATE "dwmapi.lib")'
    if anchor not in text:
        raise SystemExit(f"找不到 CMake 链接锚点: {CMAKE}")
    text = text.replace(anchor, anchor + '\ntarget_link_libraries(${BINARY_NAME} PRIVATE "imm32.lib")', 1)
    CMAKE.write_text(text, encoding="utf-8")
    print("CMakeLists.txt: patched")


def main() -> int:
    if not CPP.exists() or not CMAKE.exists():
        print("app/windows 还没生成，跳过 IME 补丁", file=sys.stderr)
        return 1
    patch_cpp()
    patch_cmake()
    return 0


if __name__ == "__main__":
    sys.exit(main())
