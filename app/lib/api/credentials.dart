import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// 设备凭据。
///
/// API Key 由服务端在配对时随机生成，**只保存在系统安全存储里**
/// （iOS Keychain / Android Keystore / Windows DPAPI / Linux libsecret），
/// 服务端只存它的 sha256 —— 所以界面上看不到，服务端也查不出明文。
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

  static Future<String?> apiKey() => _storage.read(key: _apiKeyKey);
  static Future<String?> deviceId() => _storage.read(key: _deviceIdKey);
  static Future<String?> deviceName() => _storage.read(key: _deviceNameKey);
  static Future<String?> username() => _storage.read(key: _usernameKey);

  static Future<void> save({
    required String apiKey,
    required String deviceId,
    required String deviceName,
    String? username,
  }) async {
    await _storage.write(key: _apiKeyKey, value: apiKey);
    await _storage.write(key: _deviceIdKey, value: deviceId);
    await _storage.write(key: _deviceNameKey, value: deviceName);
    if (username != null && username.isNotEmpty) {
      await _storage.write(key: _usernameKey, value: username);
    }
  }

  static Future<void> clear() async {
    await _storage.delete(key: _apiKeyKey);
    await _storage.delete(key: _deviceIdKey);
    await _storage.delete(key: _deviceNameKey);
    await _storage.delete(key: _usernameKey);
  }
}
