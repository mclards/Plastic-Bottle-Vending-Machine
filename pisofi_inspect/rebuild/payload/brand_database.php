<?php
$cfg=parse_ini_file('/etc/environment');
$db=new PDO('mysql:unix_socket=/run/mysqld/mysqld.sock;dbname=pisofi',$cfg['KCFGDBU'],$cfg['KCFGDBP'],[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
$db->beginTransaction();
$q=$db->prepare('SELECT setting_value FROM settings WHERE setting_key=?');
$q->execute(['portal_settings']);$raw=$q->fetchColumn();
$settings=$raw===false?[]:json_decode($raw,true);
if(!is_array($settings))throw new Exception('Invalid portal settings; refusing to replace them');
$old=$settings;
$settings['site_info']=['site_name'=>'ECO-Fi','tagline'=>'Turning Plastic into Connectivity','logo'=>'ecofi/logo.jpg','icon'=>'ecofi/favicon.ico','last_update'=>'ecofi-brand-v1'];
// Keep functional settings, rates and sessions as provisioned in the tested base.
$stmt=$db->prepare('INSERT INTO settings(setting_key,setting_value) VALUES (?,?) ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value)');
$stmt->execute(['portal_settings',json_encode($settings)]);
$stmt->execute(['ecofi_brand_version','1']);
$unchanged=$settings;unset($unchanged['site_info']);unset($old['site_info']);
if($unchanged!==$old)throw new Exception('Unexpected functional settings change');
$db->commit();
echo json_encode(['site_info'=>$settings['site_info'],'functional_portal_settings_preserved'=>true,'users'=>(int)$db->query('SELECT COUNT(*) FROM users')->fetchColumn(),'network_records'=>(int)$db->query('SELECT COUNT(*) FROM networks')->fetchColumn()],JSON_PRETTY_PRINT),"\n";
