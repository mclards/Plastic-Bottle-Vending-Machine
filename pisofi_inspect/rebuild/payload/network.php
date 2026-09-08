<?php
// Root-only bench network adaptation. Business records remain in the original schema.
try {
    $cfg=parse_ini_file('/etc/environment');
    $db=new PDO('mysql:unix_socket=/run/mysqld/mysqld.sock;dbname=pisofi',$cfg['KCFGDBU'],$cfg['KCFGDBP'],[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
    $defaults=json_decode(file_get_contents('/etc/ecofi/network-defaults.json'),true);
    $dual=isset($argv[1]) ? $argv[1]==='--dual' : is_dir('/sys/class/net/eth1');
    $mode=$dual?'dual':'single';
    $oldMode=$db->query("SELECT setting_value FROM settings WHERE setting_key='ecofi_network_mode'")->fetchColumn();
    if($oldMode===$mode){echo 'unchanged';exit(0);}
    $lan=$defaults['eth1']; $wan=$defaults['eth0'];
    $wan['config']=null; $lan['config']=null;
    if($dual) {
        $wan['status']=1; $wan['is_wan']=1; $wan['is_main']=0; $wan['dhcp_enabled']=0;
        $lan['status']=1; $lan['is_wan']=0; $lan['is_main']=1;
        $desired=['eth0'=>$wan,'eth1'=>$lan];
    } else {
        $single=$lan;
        $single['interface_name']='eth0'; $single['ifb_name']=$wan['ifb_name'];
        $single['network_name']='Bench LAN'; $single['description']='Single-port functional test LAN';
        $disabled=$wan;$disabled['interface_name']='eth1';$disabled['ifb_name']=$lan['ifb_name'];
        $disabled['status']=2; $disabled['is_main']=0; $disabled['is_wan']=0; $disabled['dhcp_enabled']=0;
        $desired=['eth0'=>$single,'eth1'=>$disabled];
    }
    $changed=false;
    $db->beginTransaction();
    // Exchange the two unique subnet keys inside this transaction; other readers see the committed state.
    $db->exec("UPDATE networks SET subnet_id_cidr=CONCAT('ecofi-stage-',interface_name) WHERE interface_name IN ('eth0','eth1')");
    foreach($desired as $iface=>$row) {
        $get=$db->prepare('SELECT * FROM networks WHERE interface_name=?');$get->execute([$iface]);$current=$get->fetch(PDO::FETCH_ASSOC);
        if(!$current)throw new Exception('Expected base network record missing');
        $sets=[];$values=[];
        foreach($row as $key=>$value) {
            if(in_array($key,['interface_name','created_at','updated_at'],true))continue;
            if((string)$current[$key] !== (string)$value) { $sets[]='`'.$key.'`=?';$values[]=$value; }
        }
        if($sets) {$values[]=$iface;$db->prepare('UPDATE networks SET '.implode(',',$sets).' WHERE interface_name=?')->execute($values);$changed=true;}
    }
    $db->prepare("INSERT INTO settings(setting_key,setting_value) VALUES ('ecofi_network_mode',?) ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value)")->execute([$mode]);
    $db->commit();
    echo $changed?'changed':'unchanged';
} catch(Exception $ex) {
    if(isset($db)&&$db->inTransaction())$db->rollBack();
    fwrite(STDERR,'Network database is not ready: '.$ex->getMessage()."\n");
    exit(1);
}
