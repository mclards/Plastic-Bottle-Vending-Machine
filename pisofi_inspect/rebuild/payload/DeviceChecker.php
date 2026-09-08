<?php
namespace App\Pisofi\Server;
use App\Helpers\PisofiHelper;
use App\Models\PisofiSetting;
class DeviceChecker
{
    const RESULT_STATUS_OK = 'OK';
    const RESULT_STATUS_NG = 'NG';
    public static function isRegistered() { return LocalLicense::load()['valid']; }
    public static function getToken() { return PisofiHelper::encodeCipher('ECOFI-LOCAL-OWNER', PisofiSetting::getValue('cipher_key')); }
    public static function getLicenseSecured($deviceId = null) { return PisofiHelper::encodeCipher(json_encode(LocalLicense::metadata()), PisofiSetting::getValue('cipher_key')); }
    public static function getDeviceOwner() { return ['name' => 'Local Test Owner', 'email' => 'owner@localhost']; }
    public static function getRecoveryKey($new = true) { return false; }
    public static function getLastDataUpload() { return []; }
    public static function updateDeviceStatus() { return ['status' => 'OK', 'local' => true]; }
    public static function canConnect() { return false; }
    public static function canReset() { return true; }
    public static function confirmLicenseRevocation($token) { return ['status' => 'NG', 'message' => 'Use the local signed license file.']; }
}
