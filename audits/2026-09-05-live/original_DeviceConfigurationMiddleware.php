<?php

namespace App\Middleware;

use App\Pisofi\Server\DeviceChecker;
use App\Models\PisofiSetting;
use App\Helpers\Rpi;
use Carbon\Carbon;
use App\Pisofi\Server\DeviceLicense;
use App\Helpers\PisofiHelper;
use App\Pisofi\Server\UserApi;

class DeviceConfigurationMiddleware extends Middleware {


    /**
     * @param $request PSR-7
     * @param $response
     * @param $next
     * @return HTML: 2 input:hidden (CSRF NAME & CSRF VALUE)
     */
    public function __invoke ($request, $response, $next) {

        $now = Carbon::now();
        $license = json_decode(PisofiSetting::getValue('license'));
        if (!$license) {
            $last_check = Carbon::now()->subHour(10);
        }
        else {
            $last_check = (property_exists($license,"last_check") ? $license->last_check : null);
            $last_check =  (strtotime($last_check) ? Carbon::parse($last_check) : Carbon::now()->subHour(10));
        }

        $license = new DeviceLicense();


        $init = PisofiSetting::getValue('initial_registration');
        $needsRegistration = !PisofiSetting::getValue('is_registered');

        $this->container->view['csrf_token'] = [
            'name' => $this->csrf->getTokenName(),
            'value' => $this->csrf->getTokenValue(),
        ];
        if ($init || $needsRegistration) {
            $rpi = new Rpi();
            return $this->view->render($response, 'errors/notregistered.twig', compact('rpi'));
        }
        if (!$license || $last_check->diffInDays($now) > 14 || ($license && $license->isTrial() && $last_check->diffInDays($now) > 1) || $license->isExpired()) {
            $internet = PisofiSetting::getValue('online');
            if ($internet) {
                $rpi = new Rpi();
                if (!DeviceChecker::isRegistered())
                {
                    PisofiSetting::setValue('is_registered', 0);
                    return $this->view->render($response, 'errors/notregistered.twig', compact('rpi'));
                } else {
                    PisofiSetting::setValue('is_registered', 1);
                    PisofiSetting::setValue('initial_registration', 0);
                }

                $access_token = PisofiSetting::getValue('access_token');
                if (!$access_token || !UserApi::getKey()) {
                    $cipher = DeviceChecker::getToken();
                    $key = PisofiSetting::getValue('cipher_key');
                    $token = $this->_decodeCipher($cipher, $key);
                    if ($token) {
                        PisofiSetting::setValue('access_token', $token);
                    } else {
                        return $response->withHeader('Content-Type', 'text/plain')->write("Device [".$rpi->serial()."] ownership validation has failed.");
                    }
                }

                if (!UserApi::getKey()){
                    $this->container->flash->addMessage('error', "Can't connect using the Access Token. Please configure the API Access Token.");
                    return $response->withRedirect($this->router->pathFor('settings.api'));
                }
                $serverLicenseRaw = DeviceChecker::getLicenseSecured();
                $key = PisofiSetting::getValue('cipher_key');
                $licenseEncoded = PisofiHelper::decodeCipher($serverLicenseRaw, $key);
                if ($licenseEncoded) {
                    $serverLicense = json_decode($licenseEncoded, true);
                } else {
                    $serverLicense = [];
                }
    
                $rpi = new Rpi();
                if (!$serverLicense)
                {
                    return $this->view->render($response, 'errors/nolicense.twig', compact('rpi'));
                }
                else {
                    if (isset($serverLicense['license'])) {
                        PisofiHelper::updateDeviceLicenseHash($rpi->serial(), isset($serverLicense['license']) ? $serverLicense['license'] : 'TRIAL');
                        $serverLicense['last_check'] = (Carbon::now())->format('Y-m-d H:i:s');
                        $serverLicense['actor'] = 'device_configuration_middleware';
                        PisofiSetting::setValue('license', json_encode($serverLicense));
                        PisofiSetting::deleteSetting('last_check_wo_net');
                        $license = new DeviceLicense();
                        if ($license->isExpired()) {
                            $this->container->flash->addMessage('error', "Your license has expired. Please configure your device to use a new one or buy from our distributors: <a href='https://pisofiph.com/distributors'>Distributors</a>");
                            return $this->view->render($response, 'errors/nolicense.twig', compact('rpi'));
                        }
                    }
                }
            }
            else {
                $lastCheckWithNoInter = PisofiSetting::find('last_check_wo_net');
                if (!$lastCheckWithNoInter ) {
                    $lastCheckWithNoInter = PisofiSetting::setValue('last_check_wo_net', (Carbon::create($now->year, $now->month, $now->day + 7, 23, 59, 59))->format('Y-m-d H:i:s'));
                }

                $last = Carbon::parse($lastCheckWithNoInter->setting_value);
                $diff = ($last ? $last->diffInDays($now) : 0);
                if ($diff > 7) {
                    $this->container->flash->addMessage('error', "<strong>License Verification Failed.</strong><br><br>Your machine does not have internet connection.<br>Please check your cables.<br><br>Access to admin page has been disabled.");
                    return $this->view->render($response, 'errors/licenseverification.twig');
                } else {
                    $this->container->flash->addMessage('error', "<strong>License Verification Failed.</strong><br><br>Your machine does not have internet connection.<br>Please check your cables.<br><br>If this continues, you may not be able to access the admin page. Please fix this issue in $diff day(s)");
                }
            }
        }

        $response = $next($request, $response);
        return $response;
    }

    private function _decodeCipher($ciphertext, $key)
    {
        $c = base64_decode($ciphertext);
        $ivlen = openssl_cipher_iv_length($cipher="AES-128-CBC");
        $iv = substr($c, 0, $ivlen);
        $hmac = substr($c, $ivlen, $sha2len=32);
        $ciphertext_raw = substr($c, $ivlen+$sha2len);
        $original_plaintext = openssl_decrypt($ciphertext_raw, $cipher, $key, $options=OPENSSL_RAW_DATA, $iv);
        $calcmac = hash_hmac('sha256', $ciphertext_raw, $key, $as_binary=true);
        if (hash_equals($hmac, $calcmac))//PHP 5.6+ timing attack safe comparison
        {
            return $original_plaintext;
        }
        return false;

    }

}