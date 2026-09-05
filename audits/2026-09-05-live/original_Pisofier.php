<?php

namespace App\Pisofi;

use mikehaertl\shellcommand\Command;
class Pisofier
{
    public function connect($mac, $ip, $mark, $downloadRate, $uploadRate, $ceilRate){
        $command = new Command("sudo pisofier connect {$mac} {$ip} {$mark} {$downloadRate} {$uploadRate} {$ceilRate} > /dev/null 2>&1 & ");
        if ($command->execute()) {
            return $command->getOutput();
        }
        return false;
    }
    public function disconnect($mac, $ip, $mark, $downloadRate, $uploadRate, $ceilRate){
        $command = new Command("sudo pisofier disconnect {$mac} {$ip} {$mark} {$downloadRate} {$uploadRate} {$ceilRate} > /dev/null 2>&1 & ");
        if ($command->execute()) {
            return $command->getOutput();
        }
        return false;
    }

    public function resetConnections()
    {
        $command = new Command("sudo pisofi_resetconnections > /dev/null 2>&1 & ");
        if ($command->execute()) {
            return $command->getOutput();
        }
        return false;
    }

    public function resetRules()
    {
        $command = new Command("sudo /usr/bin/php /var/www/html/pisofi/scripts/pfirules false > /dev/null 2>&1 & ");
        if ($command->execute()) {
            return $command->getOutput();
        }
        return false;
    }

    public function restartClientConnections($async = true)
    {
        $async = $async ? " > /dev/null 2>&1 &" : "";
        $command = new Command("sudo pisofier reset $async");
        if ($command->execute()) {
            return $command->getOutput();
        }
        return false;
    }

    public function resetPorts()
    {
        $command = new Command("sudo pisofier reset_ports > /dev/null 2>&1 & ");
        if ($command->execute()) {
            return $command->getOutput();
        }
        return false;
    }
}