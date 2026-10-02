import 'dart:io';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 设备凭据。
///
/// API Key 由服务端在登录时随机生成，优先存进系统安全存储
/// （iOS Keychain / Android Keystore / Windows DPAPI / Linux libsecret），
/// 服务端只存它的 sha256。
///
/// 桌面端的 secure storage 偶尔会「这个进程写进去、下个进程读不回来」，
/// 表现就是每次启动都要重新登录。所以：
/// * 写入后**读回校验**，对不上就用普通存储；
/// * Windows / Linux 上**额外留一份普通存储的副本**兜底（移动端不留）。
/// 真的退回普通存储时，「我的」页面会提示。
class Credentials {
  static const String _apiKeyKey = 'tonicuisc_api_key';
  static const String _deviceIdKey = 'tonicuisc_device_id';
  static const String _deviceNameKey = 'tonicuisc_device_name';
  static const String _usernameKey = 'tonicuisc_username';

  static const FlutterSecureStorage _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
    // 只在本机解锁后可用，且不进 iCloud 备份
    iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock_this_device),
  );

  /// true = 本机没能用系统安全存储，凭据退回普通存储（安全性略低）。
  static bool usingFallback = false;

  /// 桌面端的 secure storage 会「这个进程写进去、下个进程读不回来」（DPAPI / libsecret
  /// 环境问题），表现就是每次启动都要重新登录。所以桌面端额外留一份普通存储的副本兜底。
  static bool get _mirrorToPrefs => Platform.isWindows || Platform.isLinux;

  static String _fallbackKey(String key) => 'fallback_$key';

  static Future<String?> _read(String key) async {
    try {
      final value = await _storage.read(key: key);
      if (value != null && value.isNotEmpty) return value;
    } catch (_) {
      usingFallback = true;
    }
    final prefs = await SharedPreferences.getInstance();
    final fallback = prefs.getString(_fallbackKey(key));
    if (fallback != null && fallback.isNotEmpty) {
      if (!_mirrorToPrefs) usingFallback = true;
      return fallback;
    }
    return null;
  }

  static Future<void> _write(String key, String value) async {
    final prefs = await SharedPreferences.getInstance();
    try {
      await _storage.write(key: key, value: value);
      // 读回校验：写进去读不回来就当作不可用
      final check = await _storage.read(key: key);
      if (check == value) {
        if (_mirrorToPrefs) {
          await prefs.setString(_fallbackKey(key), value);
        } else {
          await prefs.remove(_fallbackKey(key)); // 移动端不留明文副本
        }
        return;
      }
    } catch (_) {
      // 落到下面的 fallback
    }
    usingFallback = true;
    await prefs.setString(_fallbackKey(key), value);
  }

  static Future<void> _delete(String key) async {
    try {
      await _storage.delete(key: key);
    } catch (_) {
      // 忽略：下面还要清 fallback
    }
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_fallbackKey(key));
  }

  static Future<String?> apiKey() => _read(_apiKeyKey);
  static Future<String?> deviceId() => _read(_deviceIdKey);
  static Future<String?> deviceName() => _read(_deviceNameKey);
  static Future<String?> username() => _read(_usernameKey);

  static Future<void> save({
    required String apiKey,
    required String deviceId,
    required String deviceName,
    String? username,
  }) async {
    await _write(_apiKeyKey, apiKey);
    await _write(_deviceIdKey, deviceId);
    await _write(_deviceNameKey, deviceName);
    if (username != null && username.isNotEmpty) {
      await _write(_usernameKey, username);
    }
  }

  static Future<void> clear() async {
    await _delete(_apiKeyKey);
    await _delete(_deviceIdKey);
    await _delete(_deviceNameKey);
    await _delete(_usernameKey);
  }
}
