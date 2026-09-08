<?php
// Run against the staged database during build, or manually from the local console.
$cfg = parse_ini_file('/etc/environment');
$db = new PDO('mysql:unix_socket=/run/mysqld/mysqld.sock;dbname=pisofi', $cfg['KCFGDBU'], $cfg['KCFGDBP'], [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
$hash = trim(file_get_contents('/etc/ecofi/admin-password.hash'));
if (strpos($hash, '$2y$') !== 0) throw new Exception('Missing generated administrator hash');
$db->beginTransaction();
$stmt = $db->prepare('UPDATE users SET username=?, password=?, user_type=1, active=1 WHERE id=?');
$id = $db->query('SELECT id FROM users WHERE user_type=1 ORDER BY id LIMIT 1')->fetchColumn();
if ($id === false) throw new Exception('No administrator record in the base image; schema needs review');
$stmt->execute(['administrator', $hash, $id]);
$set = $db->prepare('INSERT INTO settings(setting_key,setting_value) VALUES (?,?) ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value)');
require '/.cache/tmp/55/05/pfi/app/Pisofi/Server/LocalLicense.php';
$values = ['license' => json_encode(\App\Pisofi\Server\LocalLicense::metadata()), 'is_registered' => '1', 'initial_registration' => '0', 'system_registered' => '1',
           'registered_to' => json_encode(['username'=>'Local Test Owner','email'=>'owner@localhost','first_name'=>'Local','last_name'=>'Owner']),
           'access_token' => 'ECOFI-LOCAL-OWNER', 'needs_verifier_rerun' => '0'];
foreach ($values as $key => $value) $set->execute([$key, $value]);
$db->exec('DELETE FROM login_attempts');
$db->commit();
$counts = [];
foreach (['users','active_clients','connection_sessions','networks','settings'] as $table) $counts[$table] = (int)$db->query('SELECT COUNT(*) FROM '.$table)->fetchColumn();
echo json_encode(['provisioned'=>true,'table_counts'=>$counts, 'networks'=>$db->query('SELECT * FROM networks')->fetchAll(PDO::FETCH_ASSOC)], JSON_PRETTY_PRINT), "\n";
