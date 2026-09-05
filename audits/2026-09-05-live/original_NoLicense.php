<?php

namespace App\Pisofi\Server\License;

use Carbon\Carbon;
use App\Models\PisofiSetting;
use App\Pisofi\Server\IDeviceLicense;

class NoLicense implements IDeviceLicense
{

    public function __construct($license)
    {
    }

    public function hasLicense()
    {
        return false;
    }

    public function isTrial()
    {
        return true;
    }

    public function isLicensed()
    {
        return false;
    }

    public function licenseKey()
    {
        return "N/A";
    }

    public function registeredVendos()
    {
        return 0;
    }
    public function registeredPCs()
    {
        return 0;
    }
    public function getLicenseType()
    {
        return IDeviceLicense::NO_LICENSE;
    }

    public function getExpirationDate()
    {
        return IDeviceLicense::NO_EXPIRATION;
    }

    public function getRemainingDays()
    {
        return 0;
    }

    public function isExpired()
    {
        return true;
    }

    public function update()
    {
        PisofiSetting::setValue('license', null);
        return $this;
    }
}
