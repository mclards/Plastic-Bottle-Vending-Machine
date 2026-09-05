<?php

namespace App\Pisofi\Server;

use Carbon\Carbon;
use App\Models\PisofiSetting;

interface IDeviceLicense 
{

    const LICENSE_TYPE_TRIAL = 'TRIAL';
    const LICENSE_TYPE_LICENSED = 'LICENSED';
    const NO_EXPIRATION = "NO EXPIRATION";
    const NO_LICENSE = "NO LICENSE";

    public function hasLicense();
    
    public function isTrial();

    public function isLicensed();

    public function licenseKey();

    public function getLicenseType();

    public function getExpirationDate();

    public function getRemainingDays();

    public function isExpired();

    public function registeredVendos();

    public function registeredPCs();
}
