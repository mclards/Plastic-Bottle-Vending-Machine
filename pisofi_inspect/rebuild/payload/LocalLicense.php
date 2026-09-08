<?php
namespace App\Pisofi\Server;

/** Offline license verifier. Only the public verification key ships on the device. */
class LocalLicense
{
    public static function hardwareId()
    {
        foreach (['/sys/firmware/devicetree/base/serial-number', '/etc/ecofi/device-id'] as $path) {
            if (is_readable($path)) {
                $id = trim(file_get_contents($path), "\0\r\n\t ");
                if ($id !== '') return $id;
            }
        }
        if (is_readable('/proc/cpuinfo') && preg_match('/^Serial\s*:\s*(\S+)/m', file_get_contents('/proc/cpuinfo'), $m)) return $m[1];
        return 'UNINITIALIZED';
    }

    public static function validateDocument($document, $publicKey, $hardwareId, $now)
    {
        $bad = function ($reason) { return ['valid' => false, 'reason' => $reason, 'payload' => []]; };
        $envelope = json_decode($document, true);
        if (!is_array($envelope) || !isset($envelope['payload'], $envelope['signature']) || !is_string($envelope['payload']) || !is_string($envelope['signature'])) return $bad('Malformed license envelope');
        $bytes = base64_decode($envelope['payload'], true);
        $signature = base64_decode($envelope['signature'], true);
        if ($bytes === false || $signature === false || openssl_verify($bytes, $signature, $publicKey, OPENSSL_ALGO_SHA256) !== 1) return $bad('Invalid license signature');
        $p = json_decode($bytes, true);
        if (!is_array($p) || !isset($p['schema'], $p['issuer'], $p['scope'], $p['device'], $p['id'], $p['tier'], $p['features']) || !array_key_exists('expires_at', $p)) return $bad('Incomplete license');
        if ($p['schema'] !== 1 || $p['issuer'] !== 'ECOFI' || !in_array($p['scope'], ['TEST', 'DEVICE'], true)) return $bad('Unsupported license');
        if (!is_string($p['id']) || !preg_match('/^[A-Z0-9-]{35}$/D', $p['id']) || !is_string($p['tier']) || !is_string($p['device'])) return $bad('Invalid identity fields');
        if ($p['device'] === '*') {
            if ($p['scope'] !== 'TEST') return $bad('Wildcard is restricted to signed test licenses');
        } elseif ($hardwareId === 'UNINITIALIZED' || !hash_equals($p['device'], $hardwareId)) return $bad('Hardware identity mismatch');
        if ($p['expires_at'] !== null) {
            if (!is_int($p['expires_at']) || $p['expires_at'] <= $now) return $bad('License expired');
        }
        $f = $p['features'];
        if (!is_array($f) || !isset($f['charging'], $f['vendos'], $f['desktops']) || !is_bool($f['charging']) || !is_int($f['vendos']) || !is_int($f['desktops']) || $f['vendos'] < 0 || $f['desktops'] < 0) return $bad('Invalid feature entitlements');
        return ['valid' => true, 'reason' => 'Verified offline', 'payload' => $p];
    }

    public static function load()
    {
        if (!is_readable('/etc/ecofi/license.json') || !is_readable('/etc/ecofi/license-public.pem')) return ['valid' => false, 'reason' => 'Local license is missing', 'payload' => []];
        return self::validateDocument(file_get_contents('/etc/ecofi/license.json'), file_get_contents('/etc/ecofi/license-public.pem'), self::hardwareId(), time());
    }

    public static function metadata()
    {
        $s = self::load();
        if (!$s['valid']) return null;
        $p = $s['payload'];
        return ['licenseType' => 'LICENSED', 'license' => $p['id'],
                'charging' => $p['features']['charging'], 'vendos' => $p['features']['vendos'],
                'desktops' => $p['features']['desktops'], 'last_check' => date('Y-m-d H:i:s'),
                'actor' => 'ecofi_signed_local', 'tier' => $p['tier'], 'scope' => $p['scope']];
    }
}
