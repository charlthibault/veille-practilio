<?php
/**
 * Idempotent : garantit qu'une user query partagée existe pour un utilisateur
 * FreshRSS, et écrit son token sur stdout.
 *
 * Exécuté *dans* le conteneur FreshRSS (voir freshrss.py), paramétré par
 * variables d'environnement pour éviter toute collision avec le parseur
 * d'options de la CLI FreshRSS :
 *   FRSS_USER, FRSS_QUERY_NAME, FRSS_QUERY_GET, FRSS_QUERY_ORDER,
 *   FRSS_QUERY_STATE, FRSS_QUERY_SEARCH
 *
 * Reproduit ce que fait configureController::addQuery() (génération du token
 * avec le sel de l'instance, normalisation via FreshRSS_UserQuery).
 */
declare(strict_types=1);
require '/var/www/FreshRSS/cli/_cli.php';

function env_str(string $name, string $default = ''): string {
	$value = getenv($name);
	return is_string($value) && $value !== '' ? $value : $default;
}

$username = env_str('FRSS_USER');
$name = env_str('FRSS_QUERY_NAME');
if ($username === '' || $name === '') {
	fail('FRSS_USER et FRSS_QUERY_NAME sont requis');
}

cliInitUser($username);

$queries = FreshRSS_Context::userConf()->queries;

// La query existe déjà : on réutilise son token (setup déjà fait, volume persisté).
foreach ($queries as $i => $query) {
	if (($query['name'] ?? '') !== $name) {
		continue;
	}
	$changed = false;
	if (empty($query['token'])) {
		$query['token'] = FreshRSS_UserQuery::generateToken($name);
		$changed = true;
	}
	if (empty($query['shareRss'])) {
		// Sans partage HTML/RSS, api/query.php répond 404.
		$query['shareRss'] = true;
		$changed = true;
	}
	if ($changed) {
		$queries[$i] = $query;
		FreshRSS_Context::userConf()->queries = $queries;
		FreshRSS_Context::userConf()->save();
		fwrite(STDERR, "User query “{$name}” réparée (token/partage).\n");
	}
	echo $query['token'], "\n";
	exit(0);
}

// Sinon on la crée.
$params = [
	'get' => env_str('FRSS_QUERY_GET', 'a'),
	'order' => env_str('FRSS_QUERY_ORDER', 'DESC'),
	'state' => (int)env_str('FRSS_QUERY_STATE', '15'),
];
$search = env_str('FRSS_QUERY_SEARCH');
if ($search !== '') {
	$params['search'] = $search;
}
$params['token'] = FreshRSS_UserQuery::generateToken($name);
$params['url'] = Minz_Url::display(['params' => $params]);
$params['name'] = $name;
$params['shareRss'] = true;

$queries[] = (new FreshRSS_UserQuery($params, FreshRSS_Context::categories(), FreshRSS_Context::labels()))->toArray();
FreshRSS_Context::userConf()->queries = $queries;
FreshRSS_Context::userConf()->save();

fwrite(STDERR, "User query “{$name}” créée.\n");
echo $params['token'], "\n";
