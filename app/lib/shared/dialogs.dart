import 'package:flutter/cupertino.dart';

/// 苹果风的确认弹窗，替代 Material 的 `AlertDialog` + `showDialog`。
///
/// `showDialog` 需要 `MaterialLocalizations`，`CupertinoApp` 下没有，
/// 所以统一走 `showCupertinoDialog`。返回 `true` = 用户点了确认。
Future<bool> showConfirmDialog(
  BuildContext context, {
  required String title,
  String? message,
  String confirmText = '确定',
  String cancelText = '取消',
  bool destructive = false,
}) async {
  final result = await showCupertinoDialog<bool>(
    context: context,
    builder: (context) => CupertinoAlertDialog(
      title: Text(title),
      content: message == null ? null : Text(message),
      actions: [
        CupertinoDialogAction(
          onPressed: () => Navigator.pop(context, false),
          child: Text(cancelText),
        ),
        CupertinoDialogAction(
          isDestructiveAction: destructive,
          isDefaultAction: !destructive,
          onPressed: () => Navigator.pop(context, true),
          child: Text(confirmText),
        ),
      ],
    ),
  );
  return result ?? false;
}
