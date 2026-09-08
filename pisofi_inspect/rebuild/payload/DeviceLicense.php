<?php
namespace App\Pisofi\Server;

class DeviceLicense implements IDeviceLicense
{
    private $state;
    public function __construct() { $this->state = LocalLicense::load(); }
    public function hasLicense() { return $this->state['valid']; }
    public function isLicensed() { return $this->hasLicense(); }
    public function isTrial() { return false; }
    public function hasCharging() { return $this->hasLicense() && $this->state['payload']['features']['charging']; }
    public function registeredVendos() { return $this->hasLicense() ? $this->state['payload']['features']['vendos'] : 0; }
    public function registeredPCs() { return $this->hasLicense() ? $this->state['payload']['features']['desktops'] : 0; }
    public function licenseKey() { return $this->hasLicense() ? $this->state['payload']['id'] : 'NO LICENSE'; }
    public function getLicenseType() { return $this->hasLicense() ? self::LICENSE_TYPE_LICENSED : self::NO_LICENSE; }
    public function isExpired() { return !$this->hasLicense(); }
    public function getExpirationDate() {
        if (!$this->hasLicense() || $this->state['payload']['expires_at'] === null) return false;
        return \Carbon\Carbon::createFromTimestamp($this->state['payload']['expires_at']);
    }
    public function getRemainingDays() {
        if (!$this->hasLicense()) return 0;
        $expires = $this->state['payload']['expires_at'];
        return $expires === null ? false : max(0, (int) ceil(($expires - time()) / 86400));
    }
    public function update() { return $this; }
}
