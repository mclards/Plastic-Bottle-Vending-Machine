<?php

namespace App\Pisofi;

use App\Models\PisofiSetting;

use mikehaertl\shellcommand\Command;
use Illuminate\Database\Capsule\Manager as DB;
use App\Helpers\PisofiHelper;
use App\Helpers\IPHelper;
use App\Helpers\BoardHelper;
use App\Pisofi\Pppoe\PppoeManager;

class NetworkManager
{

    const REPEATER_ENABLED = "1";
    const REPEATER_DISABLED = "0";

    const AUTH_ENABLED = "1";
    const AUTH_DISABLED = "0";

    private $networkConfig;
    private $networkConfigSetting;
    private $dnsConfig;
    private $dnsConfigSetting;
    private $scriptSource = '/usr/src/pfi/';
    private $boardHelper;

    private $dnsChanged = false;

    private $defaultSettings = [
        'dns_server'   => [
            'code'          => 'opendns_family',
            'name'          => 'OpenDNS',
            'description'   => 'Visit <a target="_blank" href="https://signup.opendns.com/homefree/">OpenDNS Information</a>',
            'servers'       => ['208.67.222.222', '208.67.220.220']
        ],
        'domain_name'   => 'portal.pisofi.com',
        'repeater_enabled'  => self::REPEATER_DISABLED,
        'auth_enabled'  => self::AUTH_ENABLED,
        'visit_limit'   => 5,
        'lockout_time'  => 5,
        'auto_pause_wifi'        => 0,
        'auto_pause_desktop'        => 0,
        'auto_pause_charging'    => 0,
        'auto_resume_when_connected'    => 1,
        'auto_pause_when_disconnected'    => 0,
        'use_static_ip' => 1,
        'static_ip_address'  => "0.0.0.0",
        'static_routers'   => '0.0.0.0',
        'static_ip_cidr'    => 24,
        'router_accessible' => 0,
        'vlan_id'           => '11',
        'lease_time_in_hours'   => 24,
        'static_dns_servers'    => []

    ];

    private $defaultDnsServers = [
        'google' => [
            'name'  => 'Google',
            'description'   => 'Visit <a target="_blank" href="https://developers.google.com/speed/public-dns/">Google DNS Information</a>',
            'servers'   => ['8.8.8.8', '8.8.4.4']
        ],

        'quad9' => [
            'name'  => 'QUAD9',
            'description'   => 'Visit <a target="_blank" href="https://www.quad9.net/policy/">QUAD9 DNS Information</a>',
            'servers'   => ['9.9.9.9', '149.112.112.112']
        ],

        'opendns' => [
            'name'  => 'OpenDNS',
            'description'   => 'Visit <a target="_blank" href="https://signup.opendns.com/homefree/">OpenDNS Information</a>',
            'servers'   => ['208.67.222.222', '208.67.220.220']
        ],

        'opendns_family' => [
            'name'  => 'OpenDNS Family Shield',
            'description'   => 'Visit <a target="_blank" href="https://support.opendns.com/hc/en-us/articles/228007127-FamilyShield-Computer-Configuration-Instructions">OpenDNS Family Shield Information</a>',
            'servers'   => ['208.67.222.123', '208.67.220.123']
        ],

        'cloudfare' => [
            'name'  => 'Cloudfare',
            'description'   => 'Visit <a target="_blank" href="https://blog.cloudflare.com/announcing-1111/">Cloudfare DNS Information</a>',
            'servers'   => ['1.1.1.1', '1.0.0.1']
        ],

        'cleanbrowsing' => [
            'name'  => 'CleanBrowsing',
            'description'   => 'Visit <a target="_blank" href="https://blog.cloudflare.com/announcing-1111/">CleanBrowsing DNS Information</a>',
            'servers'   => ['185.228.168.9', '185.228.169.9']
        ],
        'cleanbrowsing_family_level' => [
            'name' => 'Clean Browsing Family-Level',
            'description' => 'The CleanBrowsing Family-level DNS',
            'servers' => ["185.228.168.168", "185.228.169.168"]
        ],

        'cleanbrowsing_adult_level' => [
            'name' => 'CleanBrowsing Adult-level',
            'description' => 'The CleanBrowsing Adult-level DNS',
            'servers' => ["185.228.168.10", "185.228.169.11"]
        ],

    ];

    public function __construct()
    {
        $this->networkConfig = PisofiSetting::find('network_settings');
        if (!$this->networkConfig) {
            $this->networkConfig = PisofiSetting::setValue('network_settings', json_encode($this->defaultSettings));
        }
        $this->networkConfigSetting = json_decode($this->networkConfig->setting_value, true);

        foreach ($this->defaultSettings as $key => $val) {
            if (!isset($this->networkConfigSetting[$key])) {
                $this->networkConfigSetting[$key] = $val;
            }
        }

        $this->dnsConfig = PisofiSetting::find('dns_servers');
        if (!$this->dnsConfig) {
            $this->dnsConfig = PisofiSetting::setValue('dns_servers', json_encode($this->defaultDnsServers));
        }

        $data = \json_decode($this->dnsConfig->setting_value, true);

        $this->dnsConfigSetting = PisofiHelper::array_merge_recursive_distinct($data, $this->defaultDnsServers);

        $this->boardHelper = new BoardHelper();
    }

    public function vlanId($value = null)
    {
        if (is_null($value)) {
            return $this->networkConfigSetting["vlan_id"];
        }
        return $this->networkConfigSetting["vlan_id"] = $value;
    }

    public function leaseTime($value = null)
    {
        if (is_null($value)) {
            return $this->networkConfigSetting["lease_time_in_hours"];
        }
        return $this->networkConfigSetting["lease_time_in_hours"] = max($value, 1);
    }

    public function getVlanId()
    {
        return "eth0." . $this->vlanId();
    }

    public function useStaticIp($value = null)
    {
        if (is_null($value)) {
            return $this->networkConfigSetting["use_static_ip"] ? true : false;
        }
        return $this->networkConfigSetting["use_static_ip"] = $value ? 1 : 0;
    }

    public function useStaticDnsServer($value = null)
    {
        if (is_null($value)) {
            return $this->networkConfigSetting["use_static_dns_servers"] ? true : false;
        }
        return $this->networkConfigSetting["use_static_dns_servers"] = $value ? 1 : 0;
    }

    public function staticRouters($value = null)
    {
        if (is_null($value)) {
            return $this->networkConfigSetting["static_routers"];
        }
        return $this->networkConfigSetting["static_routers"] = $value;
    }

    public function routerAccessible($value = null)
    {
        if (is_null($value)) {
            return $this->networkConfigSetting["router_accessible"] ? true : false;
        }
        return $this->networkConfigSetting["router_accessible"] = $value ? 1 : 0;
    }

    public function cidr($value = null)
    {
        if (is_null($value)) {
            return (int) $this->networkConfigSetting["static_ip_cidr"];
        }
        return $this->networkConfigSetting["static_ip_cidr"] = (int) $value;
    }

    public function staticIp($value = null)
    {
        if (is_null($value)) {
            return $this->networkConfigSetting["static_ip_address"];
        }
        return $this->networkConfigSetting["static_ip_address"] = $value;
    }

    public function staticDnsServers($value = null)
    {
        if (is_null($value)) {
            return $this->networkConfigSetting["static_dns_servers"];
        }
        return $this->networkConfigSetting["static_dns_servers"] = $value;
    }

    public function autoPauseWifi($value = null)
    {
        if (is_null($value)) {
            return $this->networkConfigSetting["auto_pause_wifi"] ? true : false;
        }
        return $this->networkConfigSetting["auto_pause_wifi"] = $value ? 1 : 0;
    }

    public function autoPauseDesktop($value = null)
    {
        if (is_null($value)) {
            return $this->networkConfigSetting["auto_pause_desktop"] ? true : false;
        }
        return $this->networkConfigSetting["auto_pause_desktop"] = $value ? 1 : 0;
    }

    public function autoResumeWhenConnected($value = null)
    {
        if (is_null($value)) {
            return $this->networkConfigSetting["auto_resume_when_connected"] ? true : false;
        }
        return $this->networkConfigSetting["auto_resume_when_connected"] = $value ? 1 : 0;
    }

    public function autoPauseWhenNotConnected($value = null)
    {
        if (is_null($value)) {
            return $this->networkConfigSetting["auto_pause_when_disconnected"] ? true : false;
        }
        return $this->networkConfigSetting["auto_pause_when_disconnected"] = $value ? 1 : 0;
    }

    public function autoPauseCharging($value = null)
    {
        if (is_null($value)) {
            return $this->networkConfigSetting["auto_pause_charging"] ? true : false;
        }
        return $this->networkConfigSetting["auto_pause_charging"] = $value ? 1 : 0;
    }

    public function getCaptivePortalUrl()
    {
        return $this->networkConfigSetting['domain_name'];
    }

    public function visitLimit($limit = false)
    {
        if ($limit === false) {
            return (int) $this->networkConfigSetting["visit_limit"];
        }
        return $this->networkConfigSetting["visit_limit"] = (int) $limit;
    }

    public function lockoutTime($time = false)
    {
        if (empty($time)) {
            return $this->networkConfigSetting["lockout_time"];
        }
        return $this->networkConfigSetting["lockout_time"] = (int) $time;
    }

    public function repeaterEnabled()
    {
        return $this->networkConfigSetting["repeater_enabled"] == self::REPEATER_ENABLED;
    }

    public function enableRepeater()
    {
        $this->enableAuth();
        return $this->networkConfigSetting["repeater_enabled"] = self::REPEATER_ENABLED;
    }

    public function disableRepeater()
    {
        return $this->networkConfigSetting["repeater_enabled"] = self::REPEATER_DISABLED;
    }

    public function authEnabled()
    {
        return $this->networkConfigSetting["auth_enabled"] == self::AUTH_ENABLED;
    }

    public function enableAuth()
    {
        return $this->networkConfigSetting["auth_enabled"] = self::AUTH_ENABLED;
    }

    public function disableAuth()
    {
        return $this->networkConfigSetting["auth_enabled"] = self::AUTH_DISABLED;
    }

    public function getCaptivePortalDomainName()
    {
        $domain = $this->networkConfigSetting['domain_name'];
        $parts = \explode(".", $domain);
        return \join(".", array_slice($parts, 0, count($parts) - 1));
    }

    public function getCaptivePortalDomainNameTLD()
    {
        $domain = $this->networkConfigSetting['domain_name'];
        $parts = \explode(".", $domain);
        return end($parts);
    }

    public function setCaptivePortalUrl($domainName)
    {
        if (!PisofiHelper::isValidDomain($domainName)) {
            return false;
        }

        $this->updateDomain($domainName);

        $this->networkConfigSetting['domain_name'] = $domainName;

        $this->networkConfig->setting_value = json_encode($this->networkConfigSetting);
        return $this->networkConfig->save();
    }

    public function updateDomainName($domainName)
    {
        if (!PisofiHelper::isValidDomain($domainName)) {
            return false;
        }

        $this->networkConfigSetting['domain_name'] = $domainName;

        $this->networkConfig->setting_value = json_encode($this->networkConfigSetting);
        return $this->networkConfig->save();
    }

    public function getDnsServers()
    {
        return $this->dnsConfigSetting;
    }

    public function getCurrentDnsServer()
    {
        return $this->networkConfigSetting['dns_server'];
    }

    public function setDnsServer($dnsServer)
    {
        if (isset($this->dnsConfigSetting[$dnsServer])) {
            $newDns = $this->dnsConfigSetting[$dnsServer];
            $newDns['code'] = $dnsServer;
            $this->networkConfigSetting['dns_server'] = $newDns;

            $this->networkConfig->setting_value = json_encode($this->networkConfigSetting);
            if ($this->networkConfig->save()) {
                return $this->updateDhcp();
            }
        }
        return false;
    }

    public function setIpSettings($useStaticIp, $staticIp, $cidr, $staticRouters)
    {
        $this->useStaticIp($useStaticIp);
        $this->staticIp($staticIp);
        $this->staticRouters($staticRouters);
        $this->cidr($cidr);

        $this->networkConfig->setting_value = json_encode($this->networkConfigSetting);
        if ($this->networkConfig->save()) {
            return $this->updateDhcp();
        }
        return false;
    }

    public function save()
    {
        $this->networkConfig->setting_value = json_encode($this->networkConfigSetting);
        return $this->networkConfig->save();
    }

    public function updateDhcp()
    {
        $board = $this->boardHelper->getBoard();
        $domain = $this->getCaptivePortalUrl();
        $this->updateDomain($domain);

        if ($board && $board['board'] == 'raspberrypi') {
            return $this->updateRpiDhcp();
        } else {
            return $this->updateOpiDhcp();
        }
    }

    private function updateOpiDhcp()
    {
        return true;
    }

    private function updateRpiDhcp()
    {
        return true;
    }

    public function updateNginx($domainName)
    {
        if (empty($domainName)) {
            $domainName = $this->networkConfigSetting['domain_name'];
        }
        $nginxConf = $this->scriptSource . 'default_nginx';
        $content = file_get_contents($nginxConf);
        $replaced = preg_replace("/portal.pisofi.com/im", $domainName, $content);
        $nginxConfFile = sys_get_temp_dir() . "/pisofi.nginx.conf";
        file_put_contents($nginxConfFile, $replaced);
        exec("sudo conf_copy $nginxConfFile /etc/nginx/sites-available/default");
    }

    public function updateHosts($domainName)
    {
        if (empty($domainName)) {
            $domainName = $this->networkConfigSetting['domain_name'];
        }
                $content = "127.0.0.1       localhost raspberrypi orangepione pisofi orangepi5
::1             localhost ip6-localhost ip6-loopback raspberrypi orangepione pisofi orangepi5
ff02::1         ip6-allnodes
ff02::2         ip6-allrouters
10.0.0.1        {$domainName}";

        $replaced = preg_replace("/portal.pisofi.com/im", $domainName, $content);
        $replaced = preg_replace('~(*BSR_ANYCRLF)\R~', "\n", $replaced);
        $hostsFile = sys_get_temp_dir() . "/pisofi.hosts";
        file_put_contents($hostsFile, $replaced);
        exec("sudo conf_copy $hostsFile /etc/hosts");
    }

    private function updateDomain($domainName)
    {
        $domainName = \filter_var($domainName, FILTER_SANITIZE_URL);
        $currentDomain = $this->networkConfigSetting['domain_name'];

        $dnsServer = $this->networkConfigSetting['dns_server'];
        $servers = $dnsServer['servers'];

        $server1 = "8.8.8.8";
        $server2 = "1.1.1.1";
        if (isset($servers[0])) {
            $server1 = $servers[0];
        }
        if (isset($servers[1])) {
            $server2 = $servers[1];
        }

        $netcard = PisofiSetting::getValue('netcard');
        $content = "conf-dir=/etc/dnsmasq.d/,pisofi_*
bogus-priv
dhcp-lease-max=20000
no-negcache
no-resolv
dns-forward-max=1024
domain-needed
bind-dynamic

domain=portal.pisofi.com
local=/portal.pisofi.com/
listen-address=10.0.0.1,127.0.0.1

interface={$netcard}      # Use the require wireless interface - usually wlan0
  dhcp-range={$netcard},10.0.0.101,10.0.31.254,255.255.224.0,{$this->leaseTime()}h

address=/portal.pisofi.com/10.0.0.1
address=/localhost/127.0.0.1
address=/raspberrypi/127.0.0.1
address=/orangepione/127.0.0.1
address=/orangepi5/127.0.0.1

address=/pisofi.com/10.0.0.1
server=/pisofi.com/10.0.0.1

#address=/connectivitycheck.gstatic.com/216.58.206.131
#address=/connectivitycheck.android.com/172.217.26.142
#address=/www.gstatic.com/216.58.206.99
address=/www.apple.com/2.16.21.112
address=/captive.apple.com/17.253.35.204
#address=/clients3.google.com/216.58.204.46
address=/www.msftconnecttest.com/13.107.4.52
address=/gsp1.apple.com/122.2.210.226
address=/www.airport.us/23.37.9.179
address=/www.apple.com.edgekey.net/23.36.228.66
address=/www.msftncsi.com/104.109.129.50
address=/connectivitycheck.platform.hicloud.com/160.44.202.175

#Below is for the future implemantation of rfc 7710 
dhcp-option=160,http://portal.pisofi.com
server=10.0.0.1
server={$server1}
server={$server2}
";

        $replaced = preg_replace("/portal.pisofi.com/im", $domainName, $content);
        $replaced = preg_replace('~(*BSR_ANYCRLF)\R~', "\n", $replaced);

        $dnsmasqFile = sys_get_temp_dir() . "/pisofi.dnsmasq.conf";
        file_put_contents($dnsmasqFile, $replaced);
        exec("sudo conf_copy $dnsmasqFile /etc/dnsmasq.conf");

        $nginxConf = $this->scriptSource . 'default_nginx';
        $content = file_get_contents($nginxConf);
        $replaced = preg_replace("/portal.pisofi.com/im", $domainName, $content);
        $nginxConfFile = sys_get_temp_dir() . "/pisofi.nginx.conf";
        file_put_contents($nginxConfFile, $replaced);
        exec("sudo conf_copy $nginxConfFile /etc/nginx/sites-available/default");


        $content = "127.0.0.1       localhost raspberrypi orangepione pisofi orangepi5
::1             localhost ip6-localhost ip6-loopback raspberrypi orangepione pisofi orangepi5
ff02::1         ip6-allnodes
ff02::2         ip6-allrouters
10.0.0.1        pisofi.com portal.pisofi.com";

        $replaced = preg_replace("/portal.pisofi.com/im", $domainName, $content);
        $replaced = preg_replace('~(*BSR_ANYCRLF)\R~', "\n", $replaced);
        $hostsFile = sys_get_temp_dir() . "/pisofi.hosts";
        file_put_contents($hostsFile, $replaced);
        exec("sudo conf_copy $hostsFile /etc/hosts");

        exec("sudo service dnsmasq restart > /dev/null 2>&1 & ");
        exec("sudo service nginx restart > /dev/null 2>&1 & ");

        $wifiName = PisofiSetting::getValue('wifi_name');
        PisofiHelper::writeIOSHotspot($wifiName, $domainName);

        return true;
    }
}
