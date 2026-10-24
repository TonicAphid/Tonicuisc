#include "flutter_window.h"

#include <optional>

#include "flutter/generated_plugin_registrant.h"

#include <imm.h>

FlutterWindow::FlutterWindow(const flutter::DartProject& project)
    : project_(project) {}

FlutterWindow::~FlutterWindow() {}

bool FlutterWindow::OnCreate() {
  if (!Win32Window::OnCreate()) {
    return false;
  }

  RECT frame = GetClientArea();

  // The size here must match the window dimensions to avoid unnecessary surface
  // creation / destruction in the startup path.
  flutter_controller_ = std::make_unique<flutter::FlutterViewController>(
      frame.right - frame.left, frame.bottom - frame.top, project_);
  // Ensure that basic setup of the controller was successful.
  if (!flutter_controller_->engine() || !flutter_controller_->view()) {
    return false;
  }
  RegisterPlugins(flutter_controller_->engine());
  SetChildContent(flutter_controller_->view()->GetNativeWindow());

  flutter_controller_->engine()->SetNextFrameCallback([&]() {
    this->Show();
  });

  // Flutter can complete the first frame before the "show window" callback is
  // registered. The following call ensures a frame is pending to ensure the
  // window is shown. It is a no-op if the first frame hasn't completed yet.
  flutter_controller_->ForceRedraw();

  return true;
}

void FlutterWindow::OnDestroy() {
  if (flutter_controller_) {
    flutter_controller_ = nullptr;
  }

  Win32Window::OnDestroy();
}

LRESULT
FlutterWindow::MessageHandler(HWND hwnd, UINT const message,
                              WPARAM const wparam,
                              LPARAM const lparam) noexcept {
  // Windows IME input context can become stale after the window loses and
  // regains focus, which breaks third-party IMEs (flutter/flutter#190042).
  // Only reset after a real focus loss: resetting on every activation tears
  // down an IME that is already composing (that made input stop immediately).
  static bool ime_reset_pending = false;
  if (message == WM_ACTIVATE && LOWORD(wparam) == WA_INACTIVE) {
    ime_reset_pending = true;
  } else if (ime_reset_pending &&
             (message == WM_SETFOCUS ||
              (message == WM_ACTIVATE && LOWORD(wparam) != WA_INACTIVE))) {
    ime_reset_pending = false;
    if (flutter_controller_ && flutter_controller_->view()) {
      ::ImmAssociateContextEx(
          flutter_controller_->view()->GetNativeWindow(), nullptr, IACE_DEFAULT);
    }
  }

  // Give Flutter, including plugins, an opportunity to handle window messages.
  if (flutter_controller_) {
    std::optional<LRESULT> result =
        flutter_controller_->HandleTopLevelWindowProc(hwnd, message, wparam,
                                                      lparam);
    if (result) {
      return *result;
    }
  }

  switch (message) {
    case WM_FONTCHANGE:
      flutter_controller_->engine()->ReloadSystemFonts();
      break;
  }

  return Win32Window::MessageHandler(hwnd, message, wparam, lparam);
}
