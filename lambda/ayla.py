"""Client minimale per il cloud Ayla Networks usato dai moduli Hisense AEH-W4B1.

Usa solo la libreria standard (urllib), così il pacchetto Lambda non ha dipendenze.
"""
import base64
import json
import time
import urllib.error
import urllib.request

from app_mappings import (AYLA_DEVICES_SERVERS, AYLA_USER_SERVERS, CELSIUS_BASED_APPS,
                          SECRET_ID_EXTRA_MAP, SECRET_ID_MAP, SECRET_MAP)

HTTP_TIMEOUT = 8  # secondi: Alexa concede ~8s complessivi alla skill


class AylaError(Exception):

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class AylaAuthError(AylaError):
    pass


class AylaLoginError(AylaAuthError):
    """Email/password rifiutate: NON va ritentato (Ayla blocca l'account dopo 5 errori)."""


def app_credentials(app):
    """Restituisce (app_id, app_secret, regione) per un'app, es. 'hisense-eu'."""
    if app not in SECRET_MAP:
        raise AylaError(f"App sconosciuta: {app}. Valori validi: {', '.join(sorted(SECRET_MAP))}")
    prefix = SECRET_ID_MAP.get(app, f'a-Hisense-{app}-field')
    if app in SECRET_ID_EXTRA_MAP:
        app_id = '-'.join((prefix, SECRET_ID_EXTRA_MAP[app], 'id'))
    else:
        app_id = '-'.join((prefix, 'id'))
    secret = base64.b64encode(SECRET_MAP[app]).decode('utf-8').rstrip('=')
    secret = secret.replace('+', '-').replace('/', '_')
    region = app[-2:] if app[-2:] in AYLA_USER_SERVERS else 'us'
    return app_id, '-'.join((prefix, secret)), region


class AylaClient:

    def __init__(self, email, password, app='hisense-eu'):
        self.email = email
        self.password = password
        self.app = app
        self.app_id, self.app_secret, region = app_credentials(app)
        self.user_server = AYLA_USER_SERVERS[region]
        self.devices_server = AYLA_DEVICES_SERVERS[region]
        self.celsius_api = app in CELSIUS_BASED_APPS
        self._access_token = None
        self._refresh_token = None
        self._expires_at = 0.0
        self._login_error = None

    # ---------------------------------------------------------------- HTTP

    def _http(self, method, url, body=None, token=None):
        data = json.dumps(body).encode('utf-8') if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header('Accept', 'application/json')
        if data is not None:
            req.add_header('Content-Type', 'application/json')
        req.add_header('Authorization', f'auth_token {token}' if token else 'none')
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', 'replace')[:300]
            if e.code in (401, 403):
                raise AylaAuthError(f'{method} {url}: HTTP {e.code} {detail}', e.code) from e
            raise AylaError(f'{method} {url}: HTTP {e.code} {detail}', e.code) from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise AylaError(f'{method} {url}: {e}') from e
        return json.loads(raw) if raw else None

    def _api(self, method, path, body=None):
        url = f'https://{self.devices_server}/apiv1/{path}'
        try:
            return self._http(method, url, body, self._token())
        except AylaLoginError:
            raise
        except AylaAuthError:
            # Token revocato/scaduto prima del previsto: nuovo login e un solo retry.
            self._access_token = None
            return self._http(method, url, body, self._token())

    # ---------------------------------------------------------------- Auth

    def _store_tokens(self, resp):
        self._access_token = resp['access_token']
        self._refresh_token = resp.get('refresh_token')
        # Margine di 5 minuti sulla scadenza.
        self._expires_at = time.time() + int(resp.get('expires_in', 3600)) - 300

    def login(self):
        if self._login_error:
            # Dopo un login rifiutato non ritentare finché la Lambda non viene riavviata
            # (es. dopo aver corretto le variabili d'ambiente): evita il blocco account.
            raise AylaLoginError(f'Login già fallito, non ritento: {self._login_error}')
        body = {
            'user': {
                'email': self.email,
                'password': self.password,
                'application': {'app_id': self.app_id, 'app_secret': self.app_secret},
            }
        }
        try:
            resp = self._http('POST', f'https://{self.user_server}/users/sign_in.json', body)
        except AylaAuthError as e:
            self._login_error = str(e)
            raise AylaLoginError(str(e)) from e
        self._store_tokens(resp)

    def _refresh(self):
        body = {'user': {'refresh_token': self._refresh_token}}
        self._store_tokens(
            self._http('POST', f'https://{self.user_server}/users/refresh_token.json', body))

    def _token(self):
        if self._access_token and time.time() < self._expires_at:
            return self._access_token
        if self._refresh_token:
            try:
                self._refresh()
                return self._access_token
            except AylaError:
                pass
        self.login()
        return self._access_token

    # ---------------------------------------------------------------- Dispositivi

    def devices(self):
        """Lista di dict con almeno: dsn, product_name, model, connection_status."""
        return [item['device'] for item in (self._api('GET', 'devices.json') or [])]

    def device(self, dsn):
        return (self._api('GET', f'dsns/{dsn}.json') or {}).get('device', {})

    def properties(self, dsn):
        """Restituisce {nome_proprietà: valore}."""
        items = self._api('GET', f'dsns/{dsn}/properties.json') or []
        return {p['property']['name']: p['property'].get('value') for p in items}

    def set_property(self, dsn, name, value):
        self._api('POST', f'dsns/{dsn}/properties/{name}/datapoints.json',
                  {'datapoint': {'value': value}})
