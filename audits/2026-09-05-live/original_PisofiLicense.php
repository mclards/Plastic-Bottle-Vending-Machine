<?php

namespace App\Pisofi\Server\License;

use Carbon\Carbon;
use App\Models\PisofiSetting;
use App\Pisofi\Server\IDeviceLicense;

class PisofiLicense implements IDeviceLicense
{
    private $licenseType;
    private $license;
    private $licenseData;

    public function __construct($license)
    {
        $this->license = $license;
        $this->licenseData = json_decode($this->license->setting_value);
        $this->licenseType = $this->licenseData->licenseType;
    }

    public function hasLicense()
    {
        return true;
    }

    public function isTrial()
    {
        return false;
    }

    public function registeredVendos()
    {
        return ($this->licenseData && property_exists($this->licenseData, "vendos")) ? $this->licenseData->vendos : 0;
    }

    public function registeredPCs()
    {
        return ($this->licenseData && property_exists($this->licenseData, "desktops")) ? $this->licenseData->desktops : 0;
    }

    public function isLicensed()
    {
        return true;
    }

    public function licenseKey()
    {
        return ($this->licenseData && property_exists($this->licenseData, "license")) ? $this->licenseData->license : "N/A";
    }

    public function getLicenseType()
    {
        return IDeviceLicense::LICENSE_TYPE_LICENSED;
    }

    public function getExpirationDate()
    {
        return false;
    }

    public function getRemainingDays()
    {
        return false;
    }

    public function isExpired()
    {
        return false;
    }

    public function update()
    {
        $this->license->setting_value = json_encode($this->licenseData);
        $this->license->save();
    }
}
