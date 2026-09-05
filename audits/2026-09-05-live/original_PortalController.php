<?php

namespace App\Controllers;

use Interop\Container\ContainerInterface;
use Illuminate\Database\Capsule\Manager as DB;
use mikehaertl\shellcommand\Command;
use Slim\Http\UploadedFile;

use App\Controllers\Controller;
use App\Helpers\BoardHelper;
use App\Helpers\IconConverter;
use App\Models\ActiveClient;
use App\Models\PisofiTicket;
use App\Models\PisofiSetting;
use App\Models\TimeTransfer;
use App\Models\ChargingClient;
use App\Models\ClientAccount;
use App\Models\UserLogout;
use App\Models\ClientSession;
use App\Models\ConnectionSession;
use App\Models\PromoRate;
use App\Models\DesktopClient;

use App\Pisofi\Portal\UserOptionSetting;
use App\Pisofi\SessionOptionsManager;
use App\Pisofi\Desktop\DesktopManager;
use App\Pisofi\DesktopManager as DM;

use App\Helpers\UploadHelper;
use App\Helpers\MacHelper;
use App\Helpers\PisofiHelper;
use App\Helpers\Rpi;
use App\Helpers\CookieAuthHelper;
use App\Helpers\IPHelper;
use App\Models\ClientDevice;
use App\Models\PisofiNetwork;
use App\Models\SpinCredit;
use App\Models\VendoSession;
use App\Pisofi\PortalManager;
use App\Pisofi\NetworkManager;

use App\Pisofi\PinConfigurationManager;
use App\Pisofi\PinConfiguration;
use App\Pisofi\Notifications\PushNotificationManager;
use App\Pisofi\PisofiServiceManager;
use App\Pisofi\Server\DeviceChecker;
use App\Pisofi\DesktopEventHandler;
use App\Pisofi\Promos\PromoPackageManager;
use App\Pisofi\MacBlocking\MacBlockingManager;

use App\Pisofi\Pisofier;

use App\Pisofi\Vendo\VendoManager;

use Respect\Validation\Validator as v;
use App\Pisofi\BootManager;
use App\Pisofi\IpTv\IpTvManager;
use App\Pisofi\Pppoe\PppoeManager;
use App\Pisofi\Rewards\RewardsManager;
use App\Pisofi\Roulette\RouletteManager;
use App\Pisofi\SessionManager;
use App\Pisofi\Throttle\WipassThrottleManager;
use Carbon\Carbon;
use Google_Service_Dfareporting_Resource_AccountActiveAdSummaries;

class PortalController extends AdminBaseController
{

    private $siteInfo;
    private $pm;
    private $pcm;
    private $desktopMgr;
    private $isWhitelisted;

    private $isPPPOEClient = false;
    private $ipInRange = true;

    public function __construct(ContainerInterface $container)
    {
        parent::__construct($container);

        $this->clients = ActiveClient::countActiveClients();
        $this->ipInRange = IPHelper::IsIpInAllSubnetRange($_SERVER['REMOTE_ADDR']);

        $pm = new PortalManager();
        $this->pm = $pm;
        $this->pcm = new PinConfigurationManager();

        if ($this->request->isXhr()) {
        } else {

            $session = null;
            $ip = $_SERVER['REMOTE_ADDR'];

            $this->isPPPOEClient = false;
            $pppoeMgr = new PppoeManager();
            if ($pppoeMgr->ipInRange($ip)) {
                $this->container->view['pppoe_client'] = true;
                $this->isPPPOEClient = true;
            }

            $mac = MacHelper::getMac($ip);

            $clientId = isset($_SESSION["client_id"]) ? $_SESSION["client_id"] : false;
            $logout = UserLogout::findSession($ip, $mac);


            if ($logout) {
                if (trim(strtolower($logout->client_id)) == trim(strtolower($clientId))) {
                    unset($_SESSION["client_id"]);
                    $clientId = null;
                    $this->client = null;
                    $pisofier = new Pisofier();
                    $pisofier->disconnect($logout->mac, $logout->ip_address, $logout->mac, 0, 0, 0);
                }
                
                $this->container->view['logged_out'] = true;
                $logout->delete();
            }

            $cookie_expiration_time = CookieAuthHelper::getExpirationTime();

            if (!$this->isPPPOEClient) {
                $key = PisofiSetting::getValue('cipher_key');
                $cipher = isset($_COOKIE['_cl_ac']) ? $_COOKIE['_cl_ac'] : null;
                if ($cipher && $this->ipInRange) {
                    $decoded = PisofiHelper::decodeCipher($cipher, $key);
                    list($id, $ts) = array_pad(explode('|', $decoded), 2, null);
                    $session = ActiveClient::find($id);

                    if ($session && $session->created_at->format('U') == $ts) {
                        if ($session->ip_address != $_SERVER['REMOTE_ADDR']) {

                            $pdo = DB::connection()->getPdo();
                            try {
                                $pdo->beginTransaction();
                                
                                ActiveClient::removeSessionsByIp($session->ip_address);
                                ActiveClient::removeSessionsByIp($ip);
                                
                                $sess = ConnectionSession::find($session->session_id);
                                if ($sess) {
                                    $sess->ip_address = $ip;
                                    $sess->mac = $mac;
                                    $sess->save();
                                    $sm = new SessionManager($sess, "connect");
                                    $sm->connect();
                                }
                                
                                PisofiTicket::updateIpAndMac($session->ip_address, $session->mac, $ip, $mac);
                                ConnectionSession::updateIpAndMac($session->ip_address, $session->mac, $ip, $mac);
                                TimeTransfer::updateIpAndMac($session->ip_address, $session->mac, $ip, $mac);
                                ChargingClient::updateIpAndMac($session->ip_address, $session->mac, $ip, $mac);
                                
                                if ($sess) {
                                    $oldSessionId = $session->id;
                                    $session = ActiveClient::findSession($ip, $mac);
                                    if ($session && $this->pm->sessionSynchronizer()) {
                                        ClientDevice::updateSessionId($session->id, $oldSessionId);
                                    }
                                }
                                
                                $pdo->commit();
                            } catch (\Exception $ex) {
                                $pdo->rollback();
                            }
                            
                            $pisofier = new Pisofier();
                            $pisofier->resetConnections();
                        }
                    } else {
                        if (isset($_COOKIE["_cl_ac"])) {
                            setcookie("_cl_ac", "", 0, "/");
                        }
                    }
                }
                
                
                if ($this->authEnabled) {
                    if ($clientId && $this->ipInRange) {
                        $client = ClientAccount::find($clientId);
                        if ($client && $client->isActive()) {
                            $this->client = $client->client_id;
                            
                            $this->desktopMgr = new DesktopManager();
                            $pc = $this->desktopMgr->getClient($ip);
                            if (!$pc) {
                                if ($client->ip_address != $ip || $client->mac != $mac) {

                                    $logout = UserLogout::findSession($client->ip_address, $client->mac);
                                    if (!$logout) {
                                        $logout = new UserLogout();
                                    }

                                    $connection = ActiveClient::findSession($client->ip_address, $client->mac);
                                    $logout->fill([
                                        'mac'           => $client->mac,
                                        'ip_address'    => $client->ip_address,
                                        'client_id'     => $client->client_id,
                                        'mark'          => $connection && $connection->mark ? $connection->mark : 0
                                    ]);

                                    $logout->save();

                                    $pdo = DB::connection()->getPdo();
                                    try {
                                        $pdo->beginTransaction();

                                        PisofiTicket::updateDetails($ip, $mac, $clientId);
                                        TimeTransfer::updateDetails($ip, $mac, $clientId);
                                        ChargingClient::updateDetails($ip, $mac, $clientId);
                                        ClientSession::updateDetails($ip, $mac, $clientId);

                                        ActiveClient::setOwner($ip, $mac, $clientId);
                                        PisofiTicket::setOwner($ip, $mac, $clientId);
                                        TimeTransfer::setOwner($ip, $mac, $clientId);
                                        ChargingClient::setOwner($ip, $mac, $clientId);
                                        ConnectionSession::setOwner($ip, $mac, $clientId);
                                        ClientSession::setOwner($ip, $mac, $clientId);
                                        VendoSession::setOwner($ip, $mac, $clientId);

                                        $account = ClientAccount::find($clientId);
                                        $account->mac = $mac;
                                        $account->ip_address = $ip;
                                        $account->save();

                                        $pdo->commit();

                                        $pisofier = new Pisofier();
                                        $pisofier->resetConnections();
                                        
                                    } catch (\Exception $e) {
                                        $pdo->rollback();
                                    }
                                } else {

                                    $pdo = DB::connection()->getPdo();
                                    $pdo->exec('SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED ');
                                    $pdo->beginTransaction();
                                    try {
                                        $pdo->beginTransaction();
                                        ActiveClient::setOwner($ip, $mac, $clientId);
                                        PisofiTicket::setOwner($ip, $mac, $clientId);
                                        TimeTransfer::setOwner($ip, $mac, $clientId);
                                        ChargingClient::setOwner($ip, $mac, $clientId);
                                        ConnectionSession::setOwner($ip, $mac, $clientId);
                                        ClientSession::setOwner($ip, $mac, $clientId);
                                        VendoSession::setOwner($ip, $mac, $clientId);
                                        $pdo->commit();
                                    } catch (\Exception $ex) {
                                        $pdo->rollback();
                                    }
                                }
                            }
                        } else {
                            unset($_SESSION["client_id"]);
                            CookieAuthHelper::clearAuthCookie();
                            $this->client = null;
                        }
                    }
                } else {
                    unset($_SESSION["client_id"]);
                    $this->client = null;
                }

                $actClient = null;
                if ($this->client) {
                    $session = ClientSession::findByClient($this->client);
                } else {
                    $session = ClientSession::findSession($ip, $mac);
                }

                if ($this->pm->sessionCookies() && $this->ipInRange) {
                    if ($session) {
                        $key = PisofiSetting::getValue('cipher_key');
                        $cipher = PisofiHelper::encodeCipher($session->id, $key);
                        setcookie("_cl_ss", $cipher, $cookie_expiration_time, "/");
                    } else {
                        $key = PisofiSetting::getValue('cipher_key');
                        $cipher = isset($_COOKIE['_cl_ss']) ? $_COOKIE['_cl_ss'] : null;

                        if ($cipher) {
                            $decoded = PisofiHelper::decodeCipher($cipher, $key);
                            $session = ClientSession::find($decoded);

                            if ($session) {

                                if ($session->ip_address != $_SERVER['REMOTE_ADDR']) {
                                    $session->ip_address = $_SERVER['REMOTE_ADDR'];
                                    $exists = ClientSession::findSession($session->ip_address, $_SESSION['REMOTE_ADDR']);
                                    if (!$exists) {
                                        $session->save();
                                    } else {
                                        if (!empty(trim($session->client_id))) {
                                            ClientSession::updateDetails($session->ip_address, $session->mac, $session->client_id);
                                        }
                                    }
                                }
                            }
                        } else {
                            if (isset($_COOKIE['_cl_ss'])) {
                                setcookie("_cl_ss", "", 0, "/");
                            }
                        }
                    }
                }
            }
            $internet = PisofiSetting::getValue('online');
            $internet = $internet ? true : false;
            $interfaceModel = PisofiNetwork::getByIp($ip);
            $this->container->view['interface_model'] = $interfaceModel;
            $this->container->view['interface'] = $interfaceModel ? ( $interfaceModel->isMain() ? 'main' : $interfaceModel->getInterfaceName() ) : '';
            $this->container->view['internet'] = $internet;
            $this->container->view['client_session'] = $session;

            $this->container->view["client_id"] = $this->client;
            $this->container->view["crm"]   = $this->crm;

            $info = [
                'site_name' => $pm->getSiteName(),
                'tagline' => $pm->getSiteTagLine(),
                'icon'  => $pm->getSiteIcon(),
                'logo'  => $pm->getSiteLogo()
            ];
            $this->siteInfo = $info;
            $this->container->view['site_info'] = $this->siteInfo;
            $this->container->view['pcm'] = $this->pcm;
            $this->container->view['pm'] = $this->pm;
            $this->container->view['chat_enabled'] = $this->pm->allowChat();
            $this->container->view['is_mobile'] = true;
            $this->container->view["domain_name"] = $this->networkMgr->getCaptivePortalUrl();
            $this->container->view["client_count"] = $this->clients;

            // Get All Blocked Macs
            $mbMgr = new MacBlockingManager();
            $allowedMacs = $mbMgr->getAllowedMacs();

            $tvMgr = new IpTvManager();
            $this->container->view['tv_enabled'] = $tvMgr->enabled();

            $this->isWhitelisted = isset($allowedMacs[$mac]) ? $allowedMacs[$mac] : false;
            $this->container->view['whitelisted'] = $this->isWhitelisted;
            $this->container->view['mac_control_enabled'] = $mbMgr->enabled();
        }
    }

    public function index($request, $response, $args)
    {

        $push = new PushNotificationManager();
        $ip = $request->getServerParams()['REMOTE_ADDR'];
        $view = $request->getParam('view');
        $internet = true;

        $server = $_SERVER['SERVER_ADDR'];
        $scheme = $_SERVER['REQUEST_SCHEME'];
        $mac = MacHelper::getMac($ip);
        $vendoMgr = new VendoManager();
        $chargeClient = null;

        $desktopMgr = $this->desktopMgr ? $this->desktopMgr : new DesktopManager();
        $pc = $desktopMgr->getClient($ip);
        if ($pc) {
            return $response->withRedirect($this->router->pathFor('desktop.home'));
        }

        if ($this->ipInRange) {
            if ($this->shouldUseSession() && $this->isClientSignedIn()) {
                $client = ActiveClient::findByClientId($this->client);
                $transferCode = TimeTransfer::findByClientId($this->client);
                $chargeClient = ChargingClient::findByClientId($this->client);

                if ($client && $client->ip_address != $ip && $client->mac != $mac) {
                    $sess = ConnectionSession::find($client->session_id);
                    if ($sess) {
                        $client->delete();
                        $sess->ip_address = $ip;
                        $sess->mac = $mac;
                        $sess->save();
                        $sm = new SessionManager($sess, "connect");
                        $sm->connect();
                        $oldSessionId = $client->id;
                        $client = ActiveClient::findByClientId($this->client);
                        if ($client && $this->pm->sessionSynchronizer()) {
                            ClientDevice::updateSessionId($client->id, $oldSessionId);
                        }
                    }
                }
                if ($client) {
                    $key = PisofiSetting::getValue('cipher_key');
                    $t = $client->id . '|' . $client->created_at->format('U');
                    $cipher = PisofiHelper::encodeCipher($t, $key);
                    setcookie("_cl_ac", $cipher, CookieAuthHelper::getExpirationTime(), "/");
                }
            } else {
                $client = ActiveClient::findSession($ip, $mac);
                $transferCode = TimeTransfer::findRecord($ip, $mac);
                $chargeClient = ChargingClient::findSession($ip, $mac);

                if ($client && $client->ip_address != $ip && $client->mac != $mac) {
                    $sess = ConnectionSession::find($client->session_id);
                    if ($sess) {
                        $client->delete();
                        $sess->ip_address = $ip;
                        $sess->mac = $mac;
                        $sess->save();
                        $sm = new SessionManager($sess, "connect");
                        $sm->connect();
                        $oldSessionId = $client->id;
                        $client = ActiveClient::findSession($ip, $mac);
                        if ($client && $this->pm->sessionSynchronizer()) {
                            ClientDevice::updateSessionId($client->id, $oldSessionId);
                        }
                    }
                }

                if ($client) {
                    $key = PisofiSetting::getValue('cipher_key');
                    $t = $client->id . '|' . $client->created_at->format('U');
                    $cipher = PisofiHelper::encodeCipher($t, $key);
                    setcookie("_cl_ac", $cipher, CookieAuthHelper::getExpirationTime(), "/");
                }
            }

            if ($chargeClient) {

                if (stripos($chargeClient->pin_name, 'user_charging_') !== false) {
                    $station = $this->pcm->getPinByName($chargeClient->pin_name);
                    $this->container->view['vendo'] = 'MAIN';
                } else {
                    list($stationIp, $pin) = explode("___", $chargeClient->pin_name);
                    $vendo = $vendoMgr->getVendo($stationIp);
                    if ($vendo) {
                        $station = $vendo->getChargingStation($chargeClient->pin_name);
                        $this->container->view['vendo'] = $vendo->getName();
                    }
                }
                if ($station) {
                    $this->container->view["station"] = [
                        "data"      => $station,
                        "client"    => $chargeClient,
                    ];
                }
            }
        } else {
            $this->container->view['readonly'] = true;
        }

        $sessions = ConnectionSession::countAllSessions($ip, $mac);
        $som = new SessionOptionsManager();
        $currentSession = ConnectionSession::getActiveSession($ip, $mac);

        $this->container->view['networkMgr'] = $this->networkMgr;
        $this->container->view['som'] = $som;


        $promoCount = PromoRate::getRunningPromosCount();

        if ($this->pm->allowServices()) {
            $promoMgr = new PromoPackageManager();
            $data = array_values($promoMgr->getPromos());
            $availablePromos = intval(count(array_filter($data, function ($d) {
                return $d->isAvailable();
            }))) ?? 0;
        } else {
            $availablePromos = 0;
        }

        $promoCount += $availablePromos;
        $this->container->view['promo_count'] = intval($promoCount ?? 0);

        if (!empty($view) && $view == 'readonly') {
            $this->container->view['readonly'] = true;
        }

        return $this->container->view->render($response, 'portal.home.twig', [
            'sessions' => $sessions, 
            'currentSession' => $currentSession, 
            'client' => $client, 
            'vendoMgr' => $vendoMgr, 
            'server' => $server, 
            'ip' => $ip, 
            'mac' => $mac, 
            'transferCode' => $transferCode, 
            'chargeClient' => $chargeClient, 
            'push' => $push, 
            'internet' => $internet
        ]);
    }

    public function chat($request, $response, $args)
    {

        if (!$this->pm->allowChat()) {
            return $response->withRedirect($this->router->pathFor('home'));
        }

        $ip = $request->getServerParams()['REMOTE_ADDR'];
        $server = $_SERVER['SERVER_ADDR'];
        $scheme = $_SERVER['REQUEST_SCHEME'];
        $mac = MacHelper::getMac($ip);

        if ($this->shouldUseSession() && $this->isClientSignedIn()) {
            $client = ActiveClient::findByClientId($this->client);
        } else {
            $client = ActiveClient::findSession($ip, $mac);
        }

        return $this->container->view->render($response, 'chat.twig', compact('client', 'server', 'ip', 'mac'));
    }

    public function services($request, $response, $args)
    {

        if (!$this->pm->allowServices() || $this->isPPPOEClient) {
            return $response->withRedirect($this->router->pathFor('home'));
        }

        $ip = $request->getServerParams()['REMOTE_ADDR'];
        $mac = MacHelper::getMac($ip);
        $client = ActiveClient::findSession($ip, $mac);
        return $this->container->view->render($response, 'portal.services.twig', compact('ip', 'mac', 'client'));
    }

    public function myAccount($request, $response, $args)
    {
        $ip = $request->getServerParams()['REMOTE_ADDR'];
        $server = $_SERVER['SERVER_ADDR'];
        $scheme = $_SERVER['REQUEST_SCHEME'];
        $mac = MacHelper::getMac($ip);

        if ($this->isPPPOEClient) {
            return $response->withRedirect($this->router->pathFor('home'));
        }

        if (empty($this->client)) {
            return $response->withRedirect($this->router->pathFor('home'));
        }

        $account = ClientAccount::find($this->client);
        if (!$account) {
            return $response->withRedirect($this->router->pathFor('home'));
        }

        $account->updateWalletAccount();
        $account = ClientAccount::find($this->client);

        $params = $request->getParams();
        $start = array_key_exists('start', $params) ? $params['start'] : date('Y-m-01');
        $end = array_key_exists('end', $params) ? $params['end'] : date('Y-m-d');

        $start = v::date('Y-m-d')->validate($start) ? $start : date('Y-m-d');
        $end = v::date('Y-m-d')->validate($end) ? $end : date('Y-m-d');

        $start = Carbon::parse($start)->setTime(0, 0, 0)->toDateTimeString();
        $end = Carbon::parse($end)->setTime(23, 59, 59)->toDateTimeString();

        $rewardsMgr = new RewardsManager();
        $rouletteMgr = new RouletteManager();

        $spinCredits = 0;
        if ($rouletteMgr->enabled()) {
            $spinCredits = $account->getAvailableSpinCredits();
        }

        return $this->container->view->render($response, 'portal.account.twig', compact('account', 'server', 'ip', 'mac', 'start', 'end', 'rewardsMgr', 'rouletteMgr', 'spinCredits'));
    }
    public function mySessions($request, $response, $args)
    {
        $ip = $request->getServerParams()['REMOTE_ADDR'];
        $mac = MacHelper::getMac($ip);

        $sessions = ConnectionSession::getAllSessions($ip, $mac);
        $som = new SessionOptionsManager();

        $pm = $this->pm;

        if ($this->shouldUseSession() && $this->isClientSignedIn()) {
            $transferCode = TimeTransfer::findByClientId($this->client);
        } else {
            $transferCode = TimeTransfer::findRecord($ip, $mac);
        }

        $current = ActiveClient::findSession($ip, $mac);

        $dto = $sessions->map(function ($s) use ($pm, $som, $transferCode, $current) {
            $props = [];
            $props['id']        = $s->id;
            $props['active']    = $s->isActive();
            $props['current']   = $current ? $current->session_id == $s->id : false;
            $props['order']     = $s->isActive() || $props['current'] ? 1 : 2;
            $props['type_description']  = $s->getType();
            $props['origin_description']  = $s->getOrigin();
            $props['origin_description']  = $s->getOrigin();
            $props['is_data']  = $s->isDataPlan();
            $props['formatted_remaining_data']  = $s->getFormattedRemainingData();
            $props['formatted_remaining_time']  = $s->formatRemainingTime();
            $props['remaining_time']  = doubleval($s->remaining_time);
            $props['connection_speed']  = $s->getConnectionSpeed();
            $props['status_description']  = $s->getStatus();
            $props['expired']  = $s->isExpired();
            $props['expiration_date']  = $s->expirationDate();
            $props['is_transferrable']  = $pm->canTransferTime() && $s->isTransferrable();

            if ($transferCode) {
                $props['transfer_code'] = $transferCode->getCode();
            } else {
                $props['transfer_code'] = null;
            }

            if ($pm->portalSessionSpeed()) {
                $props['show_speed']  = true;
            } else {
                $props['show_speed']  = false;
            }

            $pauseExceeded = $pm->maxPauseLimit() > 0 && ($current ? !$current->isDataPlan() && $current->pause_count >= $pm->maxPauseLimit() : false);

            $props['can_switch']  = ($som->sessionSwitchingEnabled() && !$pauseExceeded) || $s->isFree();
            $props['is_pausable'] = $pm->canPauseConnection() && $s->pauseAllowed() && $pm->isPausable($s->remaining_time) && !$s->isDataPlan() && !$pauseExceeded;
            $props['is_convertible'] = $pm->canUseWipass() && $s->conversionAllowed() && !$s->isExpired() && $pm->canConvertRemainingTime() && !$s->isDataPlan();
            return $props;
        })->toArray();

        usort($dto, function ($a, $b) {
            return $a['order'] > $b['order'];
        });

        return $response->withJson(['result' => $dto, 'token' => $this->token]);
    }

    public function myTickets($request, $response, $args)
    {
        $ip = $request->getServerParams()['REMOTE_ADDR'];
        $mac = MacHelper::getMac($ip);
        $server = $_SERVER['SERVER_ADDR'];

        $wipasses = [];

        if ($this->shouldUseSession() && $this->isClientSignedIn()) {
            $wipasses = PisofiTicket::forUser($this->client)->Wifi()->get();
        } else {
            if (!empty($mac)) {
                $wipasses = PisofiTicket::whereIn('status', [1, 2])
                    ->forSession($ip, $mac)
                    ->Wifi()
                    ->get();
            }
        }
        return $response->withJson(['tickets' => $wipasses, 'token' => $this->token]);
    }

    public function setDate($request, $response, $args)
    {
        $time = $request->getParam('time');
        $bootMgr = new BootManager();
        $command = new Command("sudo date -us '{$time}'");
        $result = false;
        if ($command->execute()) {
            exec("sudo systemctl start pisofi_kicker.service");
            exec("sudo /usr/bin/php /var/www/html/pisofi/scripts/pauseconnections.php");
            $bootMgr->setDateTime(true);
            $bootMgr->save();
            $result = true;
        } else {
            exec("sudo systemctl stop pisofi_kicker.service");
            $bootMgr->setDateTime(false);
            $bootMgr->save();
        }

        if ($result) {
            $data = [
                'status' => 'OK',
                'message' => 'Datetime has been set successfully',
            ];
        } else {
            $data = [
                'status' => 'NG',
                'message' => 'Failed to update date and time',
            ];
        }
        return $response->withJson(['result' => $data, 'token' => $this->token]);
    }


    public function vendos($request, $response, $args)
    {

        $ip = $request->getServerParams()['REMOTE_ADDR'];
        $server = $_SERVER['SERVER_ADDR'];
        $mac = MacHelper::getMac($ip);

        $desktopMgr = $this->desktopMgr ? $this->desktopMgr : new DesktopManager();
        $pc = $desktopMgr->getClient($ip);

        if ($this->license->isExpired()) {
            if ($pc) {
                return $response->withRedirect($this->router->pathFor('desktop.home'));
            } else {
                return $response->withRedirect($this->router->pathFor('home'));
            }
        }

        if (!$this->pm->canInsertCoin()) {
            if ($pc) {
                return $response->withRedirect($this->router->pathFor('desktop.home'));
            } else {
                return $response->withRedirect($this->router->pathFor('home'));
            }
        }

        $vendoMgr = new VendoManager();
        $vendos = $vendoMgr->getAllActiveVendos();
        $main = $vendoMgr->getMainVendoSettings();

        if (count($vendos) <= 0) {
            if ($pc) {
                return $response->withRedirect($this->router->pathFor('desktop.connect.vendo', ['vendo' => 'main']));
            } else {
                return $response->withRedirect($this->router->pathFor('portal.connect.vendo', ['vendo' => 'main']));
            }
        }

        return $this->container->view->render($response, 'portal.vendos.twig', compact('ip', 'mac', 'server', 'vendos', 'main'));
    }

    public function connect($request, $response, $args)
    {
        $ip = $request->getServerParams()['REMOTE_ADDR'];
        $server = $_SERVER['SERVER_ADDR'];
        $mac = MacHelper::getMac($ip);

        // TODO: Validation if vendo is not available or disabled
        $vendo_address = strtoupper($args["vendo"]);

        $desktopMgr = $this->desktopMgr ? $this->desktopMgr : new DesktopManager();
        $pc = $desktopMgr->getClient($ip);

        if ($vendo_address !== "MAIN") {

            $vendoMgr = new VendoManager();
            $vendo = $vendoMgr->getVendoByHashedIp($vendo_address);

            if (!$vendo) {
                if ($pc) {
                    return $response->withRedirect($this->router->pathFor('desktop.home'));
                } else {
                    return $response->withRedirect($this->router->pathFor('home'));
                }
            }

            $vendo_address = $vendo->getIp();
        }

        if ($pc) {
            return $response->withRedirect($this->router->pathFor('desktop.connect.vendo', ['vendo' => $vendo_address]));
        }

        if ($this->license->isExpired()) {
            if ($pc) {
                return $response->withRedirect($this->router->pathFor('desktop.home'));
            } else {
                return $response->withRedirect($this->router->pathFor('home'));
            }
        }

        if (!$this->pm->canInsertCoin()) {
            if ($pc) {
                return $response->withRedirect($this->router->pathFor('desktop.home'));
            } else {
                return $response->withRedirect($this->router->pathFor('home'));
            }
        }


        $pm = new PortalManager();


        if ($this->shouldUseSession() && $this->isClientSignedIn()) {
            $chargeClient = ChargingClient::findByClientId($this->client);
        } else {
            $chargeClient = ChargingClient::findSession($ip, $mac);
        }

        return $this->container->view->render($response, 'portal.connect.twig', compact('ip', 'mac', 'server', 'pm', 'chargeClient', 'vendo_address'));
    }

    public function wipass($request, $response, $args)
    {

        if (!$request->isXhr()) {
            return $response->withRedirect($this->router->pathFor('home'));
        }

        $ip = $request->getServerParams()['REMOTE_ADDR'];
        $mac = MacHelper::getMac($ip);
        $server = $_SERVER['SERVER_ADDR'];

        $wipasses = [];

        if ($this->shouldUseSession() && $this->isClientSignedIn()) {
            $wipasses = PisofiTicket::forUser($this->client)->Wifi()->get();
            $chargeClient = ChargingClient::findByClientId($this->client);
        } else {
            $chargeClient = ChargingClient::findSession($ip, $mac);
            if (!empty($mac)) {
                $wipasses = PisofiTicket::whereIn('status', [1, 2])
                    ->forSession($ip, $mac)
                    ->Wifi()
                    ->get();
            }
        }

        if ($request->isXhr()) {
            $data = [];
            foreach ($wipasses as $wipass) {
                $data[] = [
                    'code'  => $wipass->code,
                    'type'  => $wipass->getUsageType(),
                    'time'  => $wipass->isDataPlan() ? $wipass->getFormattedDataLimit() : $wipass->toReadable(),
                    'speed' => $wipass->getConnectionSpeed(),
                    'expired'   => $wipass->isExpired(),
                    'shared'    => $wipass->isShared(),
                    'sharecode' => $wipass->sharecode ?? '',
                    'expiration'    => $wipass->getExpirationDate() ? Carbon::parse($wipass->getExpirationDate())->format('M j, H:i') : '',
                ];
            }

            return $response->withJson(['result' => $data, 'token' => $this->token]);
        }

        return $this->container->view->render($response, 'portal.wipass.twig', compact('wipasses', 'ip', 'mac', 'server', 'chargeClient'));
    }

    public function tv($request, $response, $args)
    {
        $ip = $request->getServerParams()['REMOTE_ADDR'];
        $mac = MacHelper::getMac($ip);
        $server = $_SERVER['SERVER_ADDR'];
        $tvMgr = new IpTvManager();

        $currentSession = ConnectionSession::getActiveSession($ip, $mac);
        if (!($tvMgr->enabled() && ($this->isWhitelisted || $currentSession))) {
            return $response->withRedirect($this->router->pathFor('home'));
        }
        return $this->container->view->render($response, 'portal.tv.twig', compact('tvMgr', 'ip', 'mac', 'server'));
    }

    public function settings($request, $response, $args)
    {
        $pm = new PortalManager();
        return $this->container->view->render($response, 'admin/portal/settings.twig', compact('pm'));
    }

    public function postSettings($request, $response, $args)
    {
        $rules = [
            'site_name' => v::stringType()->length(1, 30),
            'tagline' => v::optional(v::stringType()->length(1, 500)),
        ];

        $validation = $this->container->validator->validate($request, $rules);
        $data = [];
        if ($validation->failed()) {
            $data = [
                'status' => 'NG',
                'message' => 'ERROR',
                'fields' => $validation->getErrors()
            ];
            return $response->withJson(['result' => $data, 'token' => $this->token]);
        }

        $site_name = $request->getParam('site_name');
        $tagline = $request->getParam('tagline');

        $pm = new PortalManager();

        $pm->setSiteName($site_name)
            ->setSiteTagLine($tagline);

        if ($pm->save()) {
            $data = [
                'status' => 'OK',
                'message' => 'Site Info has been updated successfully',
            ];
        } else {
            $data = [
                'status' => 'NG',
                'message' => 'Failed to update Site Info',
            ];
        }

        return $response->withJson(['result' => $data, 'token' => $this->token]);
    }

    public function banners($request, $response, $args)
    {
        $pm = new PortalManager();
        $banners = $pm->getBanners();
        return $this->container->view->render($response, 'admin/portal/banners.twig', compact('banners'));
    }

    public function userOptions($request, $response, $args)
    {
        $option = new PortalManager();
        $networkMgr = $this->networkMgr;
        $wpthrottle = new WipassThrottleManager();
        $vm = (new BoardHelper())->isVM();
        return $this->container->view->render($response, 'admin/portal/user_options.twig', compact('option', 'networkMgr', 'wpthrottle','vm'));
    }

    public function userOptionsPost($request, $response, $args)
    {
        $validation = $this->container->validator->validate($request, [
            'can_pause'                     =>  v::boolVal(),
            'can_convert'                   =>  v::boolVal(),
            'can_transfer'                  =>  v::boolVal(),
            'can_charge'                    =>  v::boolVal(),
            'can_pause_charge'                    =>  v::boolVal(),
            'can_eload'                    =>  v::boolVal(),
            'allow_services'                    =>  v::boolVal(),
            'allow_epins'                    =>  v::boolVal(),
            'allow_promo_packages'                    =>  v::boolVal(),
            'allow_chat'                    =>  v::boolVal(),
            'allow_chat_audio'                    =>  v::boolVal(),
            'eload_options'          =>  v::keySet(
                v::key('allow_promos', v::boolVal()),
                v::key('allow_regular', v::boolVal()),
                v::key('auto_refund', v::boolVal())
            ),
            'can_insert_coin'               =>  v::boolVal(),
            'min_coin_go_online'               =>  v::intVal(),
            'max_pause_limit'               =>  v::intVal(),
            'can_use_wipass'               =>  v::boolVal(),
            'min_time_wipass_conversion'   =>  v::intVal()->between(0, 1440),
            'min_amount_wipass_conversion'   =>  v::intVal()->between(0, 99999),
            'allow_wipass_not_shared'       =>  v::boolVal(),
            'can_limit_max_pause_time'      =>  v::boolVal(),
            'can_limit_min_pause_time'      =>  v::boolVal(),
            'repeater_enabled'       =>  v::boolVal(),
            'auth_enabled'       =>  v::boolVal(),
            'pause_validity'      =>  v::boolVal(),
            'pause_time_validity'      =>  v::intVal(),
            'auto_pause_wifi'          =>  v::boolVal(),
            'auto_resume_when_connected'          =>  v::boolVal(),
            'auto_pause_when_disconnected'          =>  v::boolVal(),
            'allow_audio'          =>  v::boolVal(),
            'show_wifi_instructions'          =>  v::boolVal(),
            'show_session_speed'          =>  v::boolVal(),
            'show_wifi_rates'          =>  v::boolVal(),
            'show_time_info'          =>  v::boolVal(),
            'ask_confirmation'          =>  v::boolVal(),
            'goonline_when_timeout'          =>  v::boolVal(),
            'auto_pause_charging'          =>  v::boolVal(),
            'site_redirection_enabled'          =>  v::boolVal(),
            'max_allow_pause_time'          =>  v::keySet(
                v::key('days', v::intVal()),
                v::key('hours', v::intVal()),
                v::key('minutes', v::intVal())
            ),
            'min_allow_pause_time'          =>  v::keySet(
                v::key('days', v::intVal()),
                v::key('hours', v::intVal()),
                v::key('minutes', v::intVal())
            ),
            'time_show_rates'          =>  v::boolVal(),
            'data_enabled'          =>  v::boolVal(),
            'data_show_rates'          =>  v::boolVal(),
            'data_allowed_in_account'          =>  v::boolVal(),
            'data_min_coin'          =>  v::intVal()->between(1, 9999),
            'allow_tethering'          =>  v::boolVal(),
            'allow_time_tethering'          =>  v::boolVal(),
            'allow_data_tethering'          =>  v::boolVal(),
            'time_show_goonline'          =>  v::boolVal(),
            'hide_insert_coin_on_no_internet'          =>  v::boolVal(),
            'session_cookies'          =>  v::boolVal(),
            'wipass_throttle_enabled'          =>  v::boolVal(),
            'wipass_throttle_attempts'          =>  v::intVal()->between(1, 999),
            'wipass_throttle_delay'          =>  v::intVal()->between(1, 9999999),
            'custom_ttl'          =>  v::stringType()
        ]);
        $output = [];
        if ($validation->failed()) {
            $output = [
                'status' => 'NG',
                'message' => "Please check the fields",
                'fields' => $validation->getErrors()
            ];
            return $response->withJson(['result' => $output, 'token' => $this->token]);
        }

        $serviceMgr = new PisofiServiceManager();

        $canPause = (bool) $request->getParam('can_pause');
        $repeaterEnabled = (bool) $request->getParam('repeater_enabled');
        $authEnabled = (bool) $request->getParam('auth_enabled');
        $canConvert = (bool) $request->getParam('can_convert');
        $canTransfer = (bool) $request->getParam('can_transfer');
        $canCharge = (bool) $request->getParam('can_charge');
        $canPauseCharge = (bool) $request->getParam('can_pause_charge');
        $canEload = (bool) $request->getParam('can_eload');
        $allowServices = (bool) $request->getParam('allow_services');
        $allowEpins = (bool) $request->getParam('allow_epins');
        $allowPromoPackages = (bool) $request->getParam('allow_promo_packages');
        $allowChat = (bool) $request->getParam('allow_chat');
        $allowChatAudio = (bool) $request->getParam('allow_chat_audio');
        $eloadOptions = $request->getParam('eload_options');
        if ($canEload && !$eloadOptions['allow_promos'] && !$eloadOptions['allow_regular']) {
            $output = [
                'status' => 'NG',
                'message' => "Please allow at least Promos or Regular",
            ];
            return $response->withJson(['result' => $output, 'token' => $this->token]);
        }

        $customTTLParam = $request->getParam('custom_ttl');
        if (strlen(trim($customTTLParam)) <= 0) {
            $customTTL = [];
        } else {
            $customTTL = explode(",", $customTTLParam);
        }

        $canInsertCoin = (bool) $request->getParam('can_insert_coin');
        $minCoinGoOnline = max(1, intval($request->getParam('min_coin_go_online')));
        $canUseWipass = (bool) $request->getParam('can_use_wipass');
        $canLimitMaxPauseTime = (bool) $request->getParam('can_limit_max_pause_time');
        $canLimitMinPauseTime = (bool) $request->getParam('can_limit_min_pause_time');
        $minCoinToConvertWipass = (int) $request->getParam('min_amount_wipass_conversion');
        $minTimeToConvertWipass = (int) $request->getParam('min_time_wipass_conversion');
        $canUseWipassNotShared = (bool) $request->getParam('allow_wipass_not_shared');
        $pauseValidity = (bool) $request->getParam('pause_validity');
        $pauseTimeValidity = (int) $request->getParam('pause_time_validity');
        $autoPauseWifi = $request->getParam('auto_pause_wifi');
        $autoResumeWhenConnected = $request->getParam('auto_resume_when_connected');
        $autoPauseWhenNotConnected = $request->getParam('auto_pause_when_disconnected');
        $showWifiInstructions = $request->getParam('show_wifi_instructions');
        $showSessionSpeed = $request->getParam('show_session_speed');
        $showWifiRates = $request->getParam('show_wifi_rates');
        $showTimeInfo = $request->getParam('show_time_info');
        $allowAudio = $request->getParam('allow_audio');
        $askConfirmation = $request->getParam('ask_confirmation');
        $goOnlineWhenTimeOut = $request->getParam('goonline_when_timeout');
        $siteRedirectionEnabled = $request->getParam('site_redirection_enabled');
        $siteRedirect = $request->getParam('site_redirect');
        $maxPauseLimit = $request->getParam('max_pause_limit');
        $timeShowRates = $request->getParam('time_show_rates');
        $dataEnabled = $request->getParam('data_enabled');
        $dataShowRates = $request->getParam('data_show_rates');
        $allowDataInAccount = $request->getParam('data_allowed_in_account');
        $dataMinCoin = $request->getParam('data_min_coin');
        $allowTethering = $request->getParam('allow_tethering');
        $allowTimeTethering = $request->getParam('allow_time_tethering');
        $allowDataTethering = $request->getParam('allow_data_tethering');
        $showGoOnlineForTime = $request->getParam('time_show_goonline');
        $hideInsertCoinOnNoInternet = $request->getParam('hide_insert_coin_on_no_internet');
        $sessionCookies = $request->getParam('session_cookies');
        $sessionSynchronizer = $request->getParam('session_synchronizer');
        $starlinkBlocker = $request->getParam('starlink_blocker');        

        $wpThrottlelMgr = new WipassThrottleManager();
        $wpThrottlelMgr->enabled($request->getParam('wipass_throttle_enabled'));
        $wpThrottlelMgr->throttleAttempts($request->getParam('wipass_throttle_attempts'));
        $wpThrottlelMgr->throttleDelay($request->getParam('wipass_throttle_delay'));
        $wpThrottlelMgr->save();

        $networkMgr = new NetworkManager();

        if ($authEnabled) {
            $networkMgr->enableAuth();
        } else {

            $allowChat = 0;

            $networkMgr->disableAuth();
            $dm = new DM();
            $dm->accountEnabled(false);

            if ($dm->save()) {
                DesktopClient::pauseActiveClients();
                PisofiHelper::publishEvent(DesktopEventHandler::CALL_DESKTOP_RELOAD, [
                    'ip'    => "ALL",
                    "status"    => "OK"
                ]);
            }
        }
        if ($repeaterEnabled) {
            $networkMgr->enableRepeater();
        } else {
            $networkMgr->disableRepeater();
        }
        $networkMgr->save();

        if (!PisofiHelper::isValidDomain($siteRedirect) && $siteRedirectionEnabled) {
            $output = [
                'status' => 'NG',
                'message' => "Please enter a valid website address",
            ];
            return $response->withJson(['result' => $output, 'token' => $this->token]);
        }

        $userOpts = new PortalManager();
        $resetRules = false;

        $maxAllowPauseTime = $request->getParam('max_allow_pause_time');
        $maxAllowedPauseTimeInSec = PisofiHelper::timeToSeconds([
            'd' => $maxAllowPauseTime["days"],
            'h' => $maxAllowPauseTime["hours"],
            'm' => $maxAllowPauseTime["minutes"],
        ]);

        $minAllowPauseTime = $request->getParam('min_allow_pause_time');
        $minAllowedPauseTimeInSec = PisofiHelper::timeToSeconds([
            'd' => $minAllowPauseTime["days"],
            'h' => $minAllowPauseTime["hours"],
            'm' => $minAllowPauseTime["minutes"],
        ]);

        if ($canLimitMaxPauseTime && $canLimitMinPauseTime && ($minAllowedPauseTimeInSec >= $maxAllowedPauseTimeInSec)) {
            $output = [
                'status' => 'NG',
                'message' => "Minimum Time Allowed to pause cannot be larger than Maximum Time Allowed to pause.",
            ];
            return $response->withJson(['result' => $output, 'token' => $this->token]);
        }

        $userOpts->togglePauseConnection($canPause);
        $userOpts->toggleRemainingTimeConversion($canConvert);
        $userOpts->toggleTransferTime($canTransfer);
        $userOpts->minimumAmountForWipassConversion($minCoinToConvertWipass);
        $userOpts->minimumTimeForWipassConversion($minTimeToConvertWipass);
        $userOpts->minimumCoinForGoOnline($minCoinGoOnline);
        $userOpts->portalWifiInstructions($showWifiInstructions);
        $userOpts->portalSessionSpeed($showSessionSpeed);
        $userOpts->portalWifiRates($showWifiRates);
        $userOpts->askConfirmation($askConfirmation);
        $userOpts->audio($allowAudio);
        $userOpts->goOnlineWhenTimeout($goOnlineWhenTimeOut);
        $userOpts->siteRedirectionEnabled($siteRedirectionEnabled);
        $userOpts->redirectSite($siteRedirect);

        $oldMaxPauseLimit = $userOpts->maxPauseLimit();
        $userOpts->maxPauseLimit($maxPauseLimit);
        $userOpts->showGoOnlineForTime($showGoOnlineForTime);
        $userOpts->showTimeSessionConnectionInfo($showTimeInfo);
        $userOpts->hideInsertCoinOnNoInternet($hideInsertCoinOnNoInternet);
        $userOpts->sessionCookies($sessionCookies);
        $userOpts->sessionSynchronizer($sessionSynchronizer);

        $oldStarlinkBlocker = $userOpts->starlinkBlocker();
        $userOpts->starlinkBlocker($starlinkBlocker);
        
        if ($oldStarlinkBlocker != $starlinkBlocker) {
            $resetRules = true;
        }

        $oldValue = $userOpts->isPauseTimeValidityEnabled();
        $oldPauseValue = $userOpts->pauseTimeValidity();
        $userOpts->togglePauseTimeValidity($pauseValidity);
        $userOpts->pauseTimeValidity($pauseTimeValidity);
        if ($oldValue != $pauseValidity || $oldPauseValue != $pauseTimeValidity || $oldMaxPauseLimit != $maxPauseLimit) {
            $serviceMgr->restartService(PisofiServiceManager::SERVICE_PISOFI_KICKER);
        }

        if ($this->license->hasCharging()) {
            $userOpts->toggleCharging($canCharge);

            $oldVal = $userOpts->pauseCharging();
            if ($oldVal != $canPauseCharge) {
                $userOpts->pauseCharging($canPauseCharge);
                $serviceMgr->restartService(PisofiServiceManager::SERVICE_PISOFI_SERVER);
            }
        }
        $userOpts->toggleEload($canEload);
        $userOpts->allowServices($allowServices);
        $userOpts->allowEpins($allowEpins);
        $userOpts->allowPromoPackages($allowPromoPackages);
        $userOpts->allowChat($allowChat);
        $userOpts->allowChatAudio($allowChatAudio);
        $userOpts->eloadConfig($eloadOptions);

        $userOpts->toggleInsertCoin($canInsertCoin);
        $userOpts->toggleWipass($canUseWipass);
        $userOpts->timeShowRates($timeShowRates);
        $userOpts->dataEnabled($dataEnabled);
        $userOpts->dataShowRates($dataShowRates);
        $userOpts->allowDataInAccount($allowDataInAccount);
        $userOpts->dataMinCoin($dataMinCoin);

        $oldAllowTethering = $userOpts->allowTethering();
        $userOpts->allowTethering($allowTethering);
        if ($allowTethering != $oldAllowTethering) {
            $resetRules = true;
        }

        $customTTL = is_array($customTTL) ? $customTTL : [];
        $userOpts->customTTLValues($customTTL);

        $oldAllowTimeTethering = $userOpts->allowTimeTethering();
        $userOpts->allowTimeTethering($allowTimeTethering);
        if ($allowTimeTethering != $oldAllowTimeTethering) {
            $resetRules = true;
        }

        $oldAllowDataTethering = $userOpts->allowDataTethering();
        $userOpts->allowDataTethering($allowDataTethering);
        if ($allowDataTethering != $oldAllowDataTethering) {
            $resetRules = true;
        }

        $this->networkMgr->autoPauseWifi($autoPauseWifi);
        $oldAutoResume = $this->networkMgr->autoResumeWhenConnected();
        $oldAutoPause  = $this->networkMgr->autoPauseWhenNotConnected();

        $this->networkMgr->autoResumeWhenConnected($autoResumeWhenConnected);
        $this->networkMgr->autoPauseWhenNotConnected($autoPauseWhenNotConnected);
        if (($autoResumeWhenConnected || ($oldAutoResume && !$autoResumeWhenConnected)) || ($autoPauseWhenNotConnected || ($oldAutoPause && !$autoPauseWhenNotConnected))) {
            $serviceMgr->restartService(PisofiServiceManager::SERVICE_PISOFI_INSPECTOR);
        }

        if ($this->license->hasCharging()) {
            $autoPauseCharging = $request->getParam('auto_pause_charging');
            $this->networkMgr->autoPauseCharging($autoPauseCharging);
        }

        $this->networkMgr->save();

        if (!$canUseWipass) {
            $userOpts->allowWipassNotShared(false);
        } else {
            $userOpts->allowWipassNotShared($canUseWipassNotShared);
        }

        if (!$canPause) {
            $userOpts->toggleMaxPauseTime(false);
            $userOpts->toggleMinPauseTime(false);
        } else {
            $userOpts->toggleMaxPauseTime($canLimitMaxPauseTime);
            $userOpts->toggleMinPauseTime($canLimitMinPauseTime);
        }

        $oldMaxAllowedPauseTimeInSec = $userOpts->portalMaximumTimeAllowedPauseInSeconds();
        $oldMinAllowedPauseTimeInSec = $userOpts->portalMinimumTimeAllowedPauseInSeconds();

        $maxAllowPauseTime = $request->getParam('max_allow_pause_time');
        $userOpts->portalMaximumTimeAllowedPause([
            'd' => $maxAllowPauseTime["days"],
            'h' => $maxAllowPauseTime["hours"],
            'm' => $maxAllowPauseTime["minutes"],
        ]);

        $minAllowPauseTime = $request->getParam('min_allow_pause_time');
        $userOpts->portalMinimumTimeAllowedPause([
            'd' => $minAllowPauseTime["days"],
            'h' => $minAllowPauseTime["hours"],
            'm' => $minAllowPauseTime["minutes"],
        ]);


        if ($userOpts->save()) {

            if ($oldMaxAllowedPauseTimeInSec != $maxAllowedPauseTimeInSec || $oldMinAllowedPauseTimeInSec != $minAllowedPauseTimeInSec) {
                $serviceMgr->restartService(PisofiServiceManager::SERVICE_PISOFI_KICKER);
            }

            if ($resetRules) {
                exec("sudo /usr/bin/php /var/www/html/pisofi/scripts/pfirules false");
            }

            $output = [
                'status' => 'OK',
                'message' => "Setting have been updated successfully.",
            ];
        } else {
            $output = [
                'status' => 'NG',
                'message' => "Failed to save settings.",
            ];
        }
        return $response->withJson(['result' => $output, 'token' => $this->token]);
    }

    public function removeBanner($request, $response, $args)
    {
        $validation = $this->container->validator->validate($request, [
            'id'         =>  v::alnum(),
        ]);
        $output = [];
        if ($validation->failed()) {
            $output = [
                'status' => 'NG',
                'message' => "Please check the fields",
                'fields' => $validation->getErrors()
            ];
            return $response->withJson(['result' => $output, 'token' => $this->token]);
        }

        $id = $request->getParam('id');

        $pm = new PortalManager();
        $banners = $pm->getBanners();

        if (isset($banners[$id])) {
            $banner = $banners[$id];
            $pm->removeBanner($id);
            $filename = $this->upload_directory . DIRECTORY_SEPARATOR . str_replace('/uploads/', '', $banner['img']);
            if (file_exists($filename)) {
                @unlink($filename);
                $filename = "Removing" . $filename;
            }
            $pm->save();
            $data = [
                'status' => 'OK',
                'message' => 'Banner has been removed successfully',
                'setting' => $banner,
                'filename'  => $filename
            ];
        } else {
            $data = [
                'status' => 'NG',
                'message' => 'Banner could not be found',
                'setting' => ''
            ];
        }
        return $response->withJson(['result' => $data, 'token' => $this->token]);
    }

    public function editCaption($request, $response, $args)
    {
        $validation = $this->container->validator->validate($request, [
            'id'         =>  v::stringType(),
            'caption'   => v::optional(v::stringType()->length(0, 100))
        ]);
        $output = [];
        if ($validation->failed()) {
            $output = [
                'status' => 'NG',
                'message' => "Please check the fields",
                'fields' => $validation->getErrors()
            ];
            return $response->withJson(['result' => $output, 'token' => $this->token]);
        }

        $id = $request->getParam('id');
        $caption = $request->getParam('caption');

        $pm = new PortalManager();
        $banners = $pm->getBanners();

        if (isset($banners[$id])) {
            $banner = $banners[$id];
            $pm->addBanner([
                'id' => $id,
                'caption' => $caption,
                'img' => $banner['img']
            ]);
            $pm->save();
            $data = [
                'status' => 'OK',
                'message' => 'Banner has been updated successfully',
            ];
        } else {
            $data = [
                'status' => 'NG',
                'message' => 'Banner could not be found',
            ];
        }
        return $response->withJson(['result' => $data, 'token' => $this->token]);
    }

    public function uploadBanner($request, $response, $argcs)
    {

        $pm = new PortalManager();
        $directory = $this->upload_directory;

        $caption = $request->getParam('caption');
        $id = uniqid();

        $uploadedFiles = $request->getUploadedFiles();
        $result = true;

        // handle single input with multiple file uploads
        foreach ($uploadedFiles['banners'] as $uploadedFile) {
            if ($uploadedFile->getError() === UPLOAD_ERR_OK) {
                $filename = UploadHelper::moveUploadedFile($directory, $uploadedFile);
                if (stripos(PisofiHelper::getUploadType($directory . DIRECTORY_SEPARATOR . $filename), 'image/') === false) {
                    $result = false;
                    unlink($directory . DIRECTORY_SEPARATOR . $filename);
                } else {
                    $pm->addBanner([
                        'id' => $id,
                        'caption' => $caption,
                        'img' => "/uploads/" . $filename
                    ]);
                    chmod($directory . DIRECTORY_SEPARATOR . $filename, 0775);
                }
            }
        }

        if ($pm->save() && $result) {
            $data = [
                'status' => 'OK',
                'message' => 'Site Info has been updated successfully',
            ];
        } else {
            $data = [
                'status' => 'NG',
                'message' => 'Failed to update Site Info',
            ];
        }

        return $response->withJson(['result' => $data, 'token' => $this->token]);
    }

    public function updateBannerPositions($request, $response, $args)
    {
        $validation = $this->container->validator->validate($request, [
            'banners'         =>  v::arrayType(),
        ]);
        $output = [];
        if ($validation->failed()) {
            $output = [
                'status' => 'NG',
                'message' => "Please check the fields",
                'fields' => $validation->getErrors()
            ];
            return $response->withJson(['result' => $output, 'token' => $this->token]);
        }

        $postBanners = $request->getParam('banners');

        $pm = new PortalManager();
        $banners = $pm->getBanners();

        $changed = false;
        foreach ($postBanners as $pb) {
            if (isset($banners[$pb['id']])) {
                $banner = $banners[$pb['id']];
                $pm->addBanner([
                    'id' => $pb['id'],
                    'caption' => $banner['caption'],
                    'img' => $banner['img'],
                    'position'  => $pb['position']
                ]);
                $changed = true;
            }
        }

        if ($changed) {
            $pm->save();
            $data = [
                'status' => 'OK',
                'message' => 'Banner positions has been updated successfully',
            ];
        } else {
            $data = [
                'status' => 'OK',
                'message' => 'Banner positions has been retained',
            ];
        }

        return $response->withJson(['result' => $data, 'token' => $this->token]);
    }

    public function uploadAudio($request, $response, $argcs)
    {

        $pm = new PortalManager();
        $directory = $this->assets_directory;

        $name = $request->getParam('name');
        $id = uniqid();

        $uploadedFiles = $request->getUploadedFiles();

        // handle single input with multiple file uploads
        $uploadedFile = $uploadedFiles['upload'];
        $result = true;
        if ($uploadedFile->getError() === UPLOAD_ERR_OK) {
            $extension = pathinfo($uploadedFile->getClientFilename(), PATHINFO_EXTENSION);

            $fname  = uniqid();
            switch ($name) {
                case 'background':
                    $fname = 'b1.' . $extension;
                    break;
                case 'success':
                    $fname = 'success_ding.' . $extension;
                    break;
                case 'insert_coin':
                    $fname = 'coin.' . $extension;
                    break;
                case 'chat_notification':
                    $fname = 'chat_notification.' . $extension;
                    break;
            }
            $filename = UploadHelper::moveUploadedFile($directory, $uploadedFile, $fname);
            chmod($directory . DIRECTORY_SEPARATOR . $filename, 0775);
            if (stripos(PisofiHelper::getUploadType($directory . DIRECTORY_SEPARATOR . $filename), 'audio/') === false) {
                $result = false;
                unlink($directory . DIRECTORY_SEPARATOR . $filename);
            } else {
                $settings = $pm->audioSettings();
                $settings[$name] = '/assets/' . $fname;

                $pm->audioSettings($settings);
            }
        }

        if ($pm->save() && $result) {
            $data = [
                'status' => 'OK',
                'message' => 'Audio has been uploaded successfully',
                'location'  => '/assets/' . $fname
            ];
        } else {
            $data = [
                'status' => 'NG',
                'message' => 'Failed to upload Audio',
            ];
        }

        return $response->withJson(['result' => $data, 'token' => $this->token]);
    }

    public function uploadLogo($request, $response, $argcs)
    {

        $pm = new PortalManager();
        $directory = $this->img_directory;

        $uploadedFiles = $request->getUploadedFiles();

        // handle single input with multiple file uploads
        foreach ($uploadedFiles['banners'] as $uploadedFile) {
            if ($uploadedFile->getError() === UPLOAD_ERR_OK) {
                $extension = pathinfo($uploadedFile->getClientFilename(), PATHINFO_EXTENSION);
                $filename = UploadHelper::moveUploadedFile($directory, $uploadedFile, 'custom.' . $extension);
                $pm->setSiteLogo('img/custom.' . $extension);
                $pm->lastSiteInfoUpdate(date('YmdHis'));
            }
        }

        if ($pm->save()) {
            $data = [
                'status' => 'OK',
                'message' => 'Site Info has been updated successfully',
            ];
        } else {
            $data = [
                'status' => 'NG',
                'message' => 'Failed to update Site Info',
            ];
        }

        return $response->withJson(['result' => $data, 'token' => $this->token]);
    }

    public function uploadIcon($request, $response, $argcs)
    {

        $pm = new PortalManager();
        $directory = $this->logo_directory;

        $uploadedFiles = $request->getUploadedFiles();

        // handle single input with multiple file uploads
        foreach ($uploadedFiles['banners'] as $uploadedFile) {
            if ($uploadedFile->getError() === UPLOAD_ERR_OK) {
                $extension = pathinfo($uploadedFile->getClientFilename(), PATHINFO_EXTENSION);
                $filename = UploadHelper::moveUploadedFile($directory, $uploadedFile, 'custom.' . $extension);
                $pm->setSiteIcon('custom.' . $extension);
                $pm->lastSiteInfoUpdate(date('YmdHis'));
            }
        }

        if ($pm->save()) {
            $data = [
                'status' => 'OK',
                'message' => 'Site Info has been updated successfully',
            ];
        } else {
            $data = [
                'status' => 'NG',
                'message' => 'Failed to update Site Info',
            ];
        }

        return $response->withJson(['result' => $data, 'token' => $this->token]);
    }

    public function customize($request, $response, $argcs)
    {

        $public_path = '/var/www/html/pisofi/public/';
        $css = '';
        $origCss = '';
        $customCss = $public_path . 'css/custom.css';
        $customCssOrig = $public_path . 'css/custom.css.orig';
        if (file_exists($customCss)) {
            $css = file_get_contents($customCss);
        }
        if (file_exists($customCssOrig)) {
            $origCss = (file_get_contents($customCssOrig));
        }
        $nm = new NetworkManager();

        $domain = $_SERVER['HTTP_HOST'];
        $scheme = $_SERVER['REQUEST_SCHEME'];
        $uri = $_SERVER['REQUEST_URI'];

        if (stripos($uri, 'ngrok') > -1) {
            $scheme = 'https';
        }

        return $this->container->view->render($response, 'admin/portal/customize.twig', compact('css', 'origCss', 'domain','scheme'));
    }

    public function audio($request, $response, $argcs)
    {
        $pm = new PortalManager();
        return $this->container->view->render($response, 'admin/portal/audio.twig', compact('pm'));
    }

    public function customizePost($request, $response, $argcs)
    {
        $public_path = '/var/www/html/pisofi/public/';
        $validation = $this->container->validator->validate($request, [
            'css'         =>  v::stringType(),
        ]);
        $output = [];
        if ($validation->failed()) {
            $output = [
                'status' => 'NG',
                'message' => "Please check the fields",
                'fields' => $validation->getErrors()
            ];
            return $response->withJson(['result' => $output, 'token' => $this->token]);
        }

        $css = $request->getParam('css');
        $cssFile = $customCss = $public_path . 'css/custom.css';
        file_put_contents($cssFile, $css);

        $data = [
            'status' => 'OK',
            'message' => 'Site Info has been updated successfully',
        ];

        return $response->withJson(['result' => $data, 'token' => $this->token]);
    }

    public function importPortalBackground($request, $response, $args)
    {
        $directory = $this->img_directory;
        $uploadedFiles = $request->getUploadedFiles();

        // handle single input with multiple file uploads
        $uploadedFile = $uploadedFiles['backup'];

        if ($uploadedFile->getError() !== UPLOAD_ERR_OK) {
            $data = [
                'status' => 'NG',
                'message' => 'Please check the uploaded backup',
            ];
            return $response->withJson(['result' => $data, 'token' => $this->token]);
        }

        $extension = pathinfo($uploadedFile->getClientFilename(), PATHINFO_EXTENSION);
        $filename = UploadHelper::moveUploadedFile($directory, $uploadedFile, 'portal_bg.' . $extension);

        $filename = $directory . '/' . $filename;

        // Check if we can open the backup file
        if (!file_exists($filename)) {
            $data = [
                'status' => 'NG',
                'message' => 'Failed to upload background image',
                'filename' => $filename
            ];
            return $response->withJson(['result' => $data, 'token' => $this->token]);
        }
        $data = [
            'status' => 'OK',
            'message' => 'Background image has been uploaded successfully',
        ];

        return $response->withJson(['result' => $data, 'token' => $this->token]);
    }
}
