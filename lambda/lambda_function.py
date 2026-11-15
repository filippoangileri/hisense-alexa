"""Skill Alexa Smart Home (API v3) per climatizzatori Hisense con modulo AEH-W4B1.

Interfacce esposte per ogni climatizzatore:
  - Alexa.PowerController        "accendi/spegni il condizionatore"
  - Alexa.ThermostatController   "imposta il condizionatore a 24 gradi", "metti in raffreddamento"
  - Alexa.TemperatureSensor      "che temperatura c'è in camera?"
  - Alexa.ModeController (x2)    modalità (incl. deumidificazione/ventilazione) e velocità ventola
  - Alexa.EndpointHealth

Variabili d'ambiente:
  AYLA_EMAIL, AYLA_PASSWORD  credenziali dell'app Hisense / Hi-Smart Life
  AYLA_APP                   'oem-eu' (app Hi-Smart AC, default), 'hisense-eu' (HiSmart Life), ...
  NAME_PREFIX                opzionale: prefisso dei nomi in Alexa (default 'Condizionatore ')
  TEMP_UNIT                  opzionale: 'C' o 'F' per forzare l'unità usata da t_temp
  ALLOWED_USER_ID            opzionale: user_id Amazon (amzn1.account...) autorizzato
"""
import json
import logging
import os
import time
import urllib.request
import uuid
from datetime import datetime, timezone

from ayla import AylaAuthError, AylaClient, AylaError

log = logging.getLogger()
log.setLevel(logging.INFO)

# ------------------------------------------------------------------ mapping Hisense

WORK_MODES = {  # t_work_mode
    0: ('FAN', {'it-IT': ['ventilazione', 'ventola', 'solo ventola'], 'en-US': ['fan only', 'fan']}),
    1: ('HEAT', {'it-IT': ['riscaldamento', 'caldo'], 'en-US': ['heat', 'heating']}),
    2: ('COOL', {'it-IT': ['raffreddamento', 'freddo', 'raffrescamento'], 'en-US': ['cool', 'cooling']}),
    3: ('DRY', {'it-IT': ['deumidificazione', 'deumidificatore', 'secco'], 'en-US': ['dry', 'dehumidify']}),
    4: ('AUTO', {'it-IT': ['automatico', 'auto'], 'en-US': ['auto', 'automatic']}),
}
WORK_MODE_BY_NAME = {name: code for code, (name, _) in WORK_MODES.items()}

FAN_SPEEDS = {  # t_fan_speed (ordine = ordine di AdjustMode)
    0: ('AUTO', {'it-IT': ['automatica', 'auto'], 'en-US': ['auto']}),
    5: ('LOWER', {'it-IT': ['minima', 'silenziosa'], 'en-US': ['lowest', 'quiet']}),
    6: ('LOW', {'it-IT': ['bassa'], 'en-US': ['low']}),
    7: ('MEDIUM', {'it-IT': ['media'], 'en-US': ['medium']}),
    8: ('HIGH', {'it-IT': ['alta'], 'en-US': ['high']}),
    9: ('HIGHER', {'it-IT': ['massima', 'turbo'], 'en-US': ['highest', 'max']}),
}
FAN_SPEED_BY_NAME = {name: code for code, (name, _) in FAN_SPEEDS.items()}
FAN_ORDER = [5, 6, 7, 8, 9]  # 'AUTO' fuori dalla scala ordinata

MODE_INSTANCE = 'AC.WorkMode'
FAN_INSTANCE = 'AC.FanSpeed'

RANGE_C = (16, 32)
RANGE_F = (61, 90)

# Client riusato tra invocazioni "calde" della Lambda (mantiene il token).
_client = None


def client():
    global _client
    if _client is None:
        _client = AylaClient(os.environ['AYLA_EMAIL'], os.environ['AYLA_PASSWORD'],
                             os.environ.get('AYLA_APP', 'oem-eu'))
    return _client


# ------------------------------------------------------------------ utilità

class SkillError(Exception):

    def __init__(self, err_type, message, extra=None):
        super().__init__(message)
        self.err_type = err_type
        self.message = message
        self.extra = extra or {}


def now_iso():
    now = datetime.now(timezone.utc)
    return now.strftime('%Y-%m-%dT%H:%M:%S.') + f'{now.microsecond // 1000:03d}Z'


def device_celsius():
    unit = os.environ.get('TEMP_UNIT', '').upper()
    if unit in ('C', 'F'):
        return unit == 'C'
    return client().celsius_api


def to_c(value, scale):
    if scale == 'FAHRENHEIT':
        return (value - 32) * 5 / 9
    if scale == 'KELVIN':
        return value - 273.15
    return value


def c_to_device(value_c):
    return value_c if device_celsius() else value_c * 9 / 5 + 32


def device_scale():
    return 'CELSIUS' if device_celsius() else 'FAHRENHEIT'


def text_names(names_by_locale):
    return [{'@type': 'text', 'value': {'text': t, 'locale': loc}}
            for loc, texts in names_by_locale.items() for t in texts]


def prop(namespace, name, value, instance=None):
    p = {'namespace': namespace, 'name': name, 'value': value,
         'timeOfSample': now_iso(), 'uncertaintyInMilliseconds': 500}
    if instance:
        p['instance'] = instance
    return p


def as_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def device_temp(value):
    """Normalizza una temperatura letta dal cloud nell'unità del dispositivo.

    Alcuni firmware riportano valori in °C anche quando l'API è in °F (es. t_temp=32
    in modalità ventilazione) e viceversa: soglia di plausibilità a 45/50.
    """
    if value is None:
        return None
    value = float(value)
    if device_celsius() and value > 50:
        return (value - 32) * 5 / 9
    if not device_celsius() and value < 45:
        return value * 9 / 5 + 32
    return value


# ------------------------------------------------------------------ stato

def read_state(dsn):
    """Legge proprietà e stato di connessione; solleva SkillError se offline."""
    c = client()
    try:
        dev = c.device(dsn)
    except AylaAuthError:
        raise
    except AylaError as e:
        if e.status == 404:
            raise SkillError('NO_SUCH_ENDPOINT', f'Dispositivo {dsn} inesistente') from e
        raise
    if dev.get('connection_status', 'Online') != 'Online':
        raise SkillError('ENDPOINT_UNREACHABLE', 'Il climatizzatore non è connesso al cloud')
    return c.properties(dsn)


def state_properties(props):
    power_on = as_int(props.get('t_power'), 0) == 1
    work = as_int(props.get('t_work_mode'), 2)
    fan = as_int(props.get('t_fan_speed'), 0)
    unit = device_scale()

    if not power_on:
        thermostat_mode = 'OFF'
    else:
        # FAN/DRY non hanno equivalente nel termostato: li segnala il ModeController.
        thermostat_mode = {1: 'HEAT', 2: 'COOL', 4: 'AUTO'}.get(work, 'AUTO')

    out = [
        prop('Alexa.PowerController', 'powerState', 'ON' if power_on else 'OFF'),
        prop('Alexa.ThermostatController', 'thermostatMode', thermostat_mode),
        prop('Alexa.ModeController', 'mode', f'WorkMode.{WORK_MODES.get(work, WORK_MODES[2])[0]}',
             MODE_INSTANCE),
        prop('Alexa.ModeController', 'mode', f'FanSpeed.{FAN_SPEEDS.get(fan, FAN_SPEEDS[0])[0]}',
             FAN_INSTANCE),
        prop('Alexa.EndpointHealth', 'connectivity', {'value': 'OK'}),
    ]
    target = device_temp(props.get('t_temp'))
    if target is not None:
        out.append(prop('Alexa.ThermostatController', 'targetSetpoint',
                        {'value': round(target), 'scale': unit}))
    room = device_temp(props.get('f_temp_in'))
    if room is not None:
        out.append(prop('Alexa.TemperatureSensor', 'temperature',
                        {'value': round(room, 1), 'scale': unit}))
    return out


# ------------------------------------------------------------------ discovery

def endpoint_for(dev, index):
    dsn = dev['dsn']
    prefix = os.environ.get('NAME_PREFIX', 'Condizionatore ')
    name = f"{prefix}{dev.get('product_name') or index + 1}".strip()
    retrievable = {'proactivelyReported': False, 'retrievable': True}

    def mode_cap(instance, names, values, ordered):
        return {
            'type': 'AlexaInterface', 'interface': 'Alexa.ModeController', 'version': '3',
            'instance': instance,
            'properties': {'supported': [{'name': 'mode'}], 'nonControllable': False,
                           **retrievable},
            'capabilityResources': {'friendlyNames': text_names(names)},
            'configuration': {
                'ordered': ordered,
                'supportedModes': [{'value': v, 'modeResources': {'friendlyNames': text_names(n)}}
                                   for v, n in values],
            },
        }

    return {
        'endpointId': dsn,
        'manufacturerName': 'Hisense',
        'friendlyName': name,
        'description': f'Climatizzatore Hisense {dev.get("model") or ""} (AEH-W4B1)'.strip(),
        'displayCategories': ['AIR_CONDITIONER'],
        'additionalAttributes': {'manufacturer': 'Hisense', 'model': str(dev.get('model') or ''),
                                 'serialNumber': dsn},
        'capabilities': [
            {'type': 'AlexaInterface', 'interface': 'Alexa', 'version': '3'},
            {'type': 'AlexaInterface', 'interface': 'Alexa.PowerController', 'version': '3',
             'properties': {'supported': [{'name': 'powerState'}], **retrievable}},
            {'type': 'AlexaInterface', 'interface': 'Alexa.ThermostatController', 'version': '3',
             'properties': {'supported': [{'name': 'targetSetpoint'}, {'name': 'thermostatMode'}],
                            **retrievable},
             'configuration': {'supportedModes': ['HEAT', 'COOL', 'AUTO', 'OFF'],
                               'supportsScheduling': False}},
            {'type': 'AlexaInterface', 'interface': 'Alexa.TemperatureSensor', 'version': '3',
             'properties': {'supported': [{'name': 'temperature'}], **retrievable}},
            mode_cap(MODE_INSTANCE,
                     {'it-IT': ['modalità', 'funzione'], 'en-US': ['mode']},
                     [(f'WorkMode.{n}', names) for n, names in WORK_MODES.values()],
                     ordered=False),
            mode_cap(FAN_INSTANCE,
                     {'it-IT': ['velocità ventola', 'ventola', 'velocità'],
                      'en-US': ['fan speed']},
                     [(f'FanSpeed.{FAN_SPEEDS[c][0]}', FAN_SPEEDS[c][1]) for c in [0] + FAN_ORDER],
                     ordered=True),
            {'type': 'AlexaInterface', 'interface': 'Alexa.EndpointHealth', 'version': '3',
             'properties': {'supported': [{'name': 'connectivity'}], **retrievable}},
        ],
    }


def handle_discovery(directive):
    devices = client().devices()
    log.info('Dispositivi Ayla: %s', json.dumps(devices, default=str))
    endpoints = [endpoint_for(d, i) for i, d in enumerate(devices) if d.get('dsn')]
    return {'event': {
        'header': {'namespace': 'Alexa.Discovery', 'name': 'Discover.Response',
                   'payloadVersion': '3', 'messageId': str(uuid.uuid4())},
        'payload': {'endpoints': endpoints},
    }}


# ------------------------------------------------------------------ comandi

def clamp_check(value_dev):
    lo, hi = RANGE_C if device_celsius() else RANGE_F
    if not lo <= value_dev <= hi:
        scale = device_scale()
        raise SkillError('TEMPERATURE_VALUE_OUT_OF_RANGE',
                         f'Temperatura fuori range ({lo}-{hi})',
                         {'validRange': {'minimumValue': {'value': lo, 'scale': scale},
                                         'maximumValue': {'value': hi, 'scale': scale}}})
    return value_dev


def apply(dsn, props, changes):
    """Scrive le proprietà sul cloud e aggiorna la copia locale dello stato."""
    c = client()
    for name, value in changes:
        c.set_property(dsn, name, value)
        props[name] = value


def handle_control(namespace, name, dsn, payload, instance):
    props = read_state(dsn)
    changes = []

    if namespace == 'Alexa.PowerController':
        changes.append(('t_power', 1 if name == 'TurnOn' else 0))

    elif namespace == 'Alexa.ThermostatController':
        if name == 'SetTargetTemperature':
            sp = payload.get('targetSetpoint')
            if not sp:
                raise SkillError('INVALID_VALUE', 'Il climatizzatore supporta un solo setpoint')
            target = round(c_to_device(to_c(sp['value'], sp['scale'])))
            changes.append(('t_temp', clamp_check(target)))
        elif name == 'AdjustTargetTemperature':
            delta = payload['targetSetpointDelta']
            delta_c = delta['value'] * 5 / 9 if delta['scale'] == 'FAHRENHEIT' else delta['value']
            delta_dev = delta_c if device_celsius() else delta_c * 9 / 5
            step = round(delta_dev) or (1 if delta_dev > 0 else -1)
            current = device_temp(props.get('t_temp'))
            if current is None:
                current = 24 if device_celsius() else 75
            changes.append(('t_temp', clamp_check(round(current) + step)))
        elif name == 'SetThermostatMode':
            mode = payload['thermostatMode']['value']
            if mode == 'OFF':
                changes.append(('t_power', 0))
            elif mode in ('HEAT', 'COOL', 'AUTO'):
                changes += [('t_work_mode', WORK_MODE_BY_NAME[mode]), ('t_power', 1)]
            else:
                raise SkillError('UNSUPPORTED_THERMOSTAT_MODE', f'Modalità {mode} non supportata')
        else:
            raise SkillError('INVALID_DIRECTIVE', f'{name} non supportata')

    elif namespace == 'Alexa.ModeController':
        if instance == MODE_INSTANCE:
            if name != 'SetMode':
                raise SkillError('INVALID_DIRECTIVE', 'La modalità non è ordinata')
            key = payload['mode'].split('.', 1)[-1]
            if key not in WORK_MODE_BY_NAME:
                raise SkillError('INVALID_VALUE', f'Modalità sconosciuta: {payload["mode"]}')
            changes += [('t_work_mode', WORK_MODE_BY_NAME[key]), ('t_power', 1)]
        elif instance == FAN_INSTANCE:
            if name == 'SetMode':
                key = payload['mode'].split('.', 1)[-1]
                if key not in FAN_SPEED_BY_NAME:
                    raise SkillError('INVALID_VALUE', f'Velocità sconosciuta: {payload["mode"]}')
                code = FAN_SPEED_BY_NAME[key]
            else:  # AdjustMode
                cur = as_int(props.get('t_fan_speed'), 7)
                idx = FAN_ORDER.index(cur) if cur in FAN_ORDER else 2
                idx = max(0, min(len(FAN_ORDER) - 1, idx + int(payload['modeDelta'])))
                code = FAN_ORDER[idx]
            changes.append(('t_fan_speed', code))
        else:
            raise SkillError('INVALID_DIRECTIVE', f'Istanza sconosciuta: {instance}')
    else:
        raise SkillError('INVALID_DIRECTIVE', f'{namespace}.{name} non supportata')

    apply(dsn, props, changes)
    return props


# ------------------------------------------------------------------ risposte

def response(directive, props, name='Response'):
    header = directive['header']
    endpoint = directive.get('endpoint', {})
    out_header = {'namespace': 'Alexa', 'name': name, 'payloadVersion': '3',
                  'messageId': str(uuid.uuid4())}
    if 'correlationToken' in header:
        out_header['correlationToken'] = header['correlationToken']
    return {
        'context': {'properties': state_properties(props)},
        'event': {'header': out_header,
                  'endpoint': {'scope': endpoint.get('scope'), 'endpointId': endpoint.get('endpointId')},
                  'payload': {}},
    }


def error_response(directive, err_type, message, extra=None):
    header = directive.get('header', {})
    # UNSUPPORTED_THERMOSTAT_MODE è un errore specifico del ThermostatController.
    namespace = 'Alexa.ThermostatController' if err_type == 'UNSUPPORTED_THERMOSTAT_MODE' \
        else 'Alexa'
    out_header = {'namespace': namespace, 'name': 'ErrorResponse', 'payloadVersion': '3',
                  'messageId': str(uuid.uuid4())}
    if 'correlationToken' in header:
        out_header['correlationToken'] = header['correlationToken']
    event = {'header': out_header,
             'payload': {'type': err_type, 'message': message, **(extra or {})}}
    endpoint = directive.get('endpoint')
    if endpoint:
        event['endpoint'] = {'endpointId': endpoint.get('endpointId')}
    return {'event': event}


# ------------------------------------------------------------------ sicurezza

_verified_tokens = {}


def check_user(directive):
    """Se ALLOWED_USER_ID è impostato, verifica il token LWA del richiedente."""
    allowed = os.environ.get('ALLOWED_USER_ID')
    if not allowed:
        return
    scope = (directive.get('endpoint') or {}).get('scope') or \
        directive.get('payload', {}).get('scope') or \
        directive.get('payload', {}).get('grantee') or {}
    token = scope.get('token')
    if not token:
        raise SkillError('INVALID_AUTHORIZATION_CREDENTIAL', 'Token mancante')
    cached = _verified_tokens.get(token)
    if cached and cached > time.time():
        return
    req = urllib.request.Request('https://api.amazon.com/user/profile',
                                 headers={'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            profile = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001
        raise SkillError('INVALID_AUTHORIZATION_CREDENTIAL', f'Token non valido: {e}') from e
    if profile.get('user_id') != allowed:
        raise SkillError('INVALID_AUTHORIZATION_CREDENTIAL', 'Utente non autorizzato')
    _verified_tokens[token] = time.time() + 600


# ------------------------------------------------------------------ entry point

def debug_dump():
    """Evento di test {"debug": "dump"}: elenca dispositivi e proprietà grezze."""
    c = client()
    out = []
    for dev in c.devices():
        try:
            props = c.properties(dev['dsn'])
        except AylaError as e:
            props = {'errore': str(e)}
        out.append({'device': dev, 'properties': props})
    return {'app': c.app, 'celsius_api': c.celsius_api, 'devices': out}


def lambda_handler(event, context):
    if event.get('debug') == 'dump':
        return debug_dump()
    directive = event.get('directive', {})
    header = directive.get('header', {})
    namespace, name = header.get('namespace'), header.get('name')
    log.info('Direttiva: %s.%s', namespace, name)

    try:
        if namespace == 'Alexa.Authorization' and name == 'AcceptGrant':
            return {'event': {'header': {'namespace': 'Alexa.Authorization',
                                         'name': 'AcceptGrant.Response', 'payloadVersion': '3',
                                         'messageId': str(uuid.uuid4())},
                              'payload': {}}}

        check_user(directive)

        if namespace == 'Alexa.Discovery' and name == 'Discover':
            return handle_discovery(directive)

        dsn = directive.get('endpoint', {}).get('endpointId')
        if namespace == 'Alexa' and name == 'ReportState':
            return response(directive, read_state(dsn), name='StateReport')

        props = handle_control(namespace, name, dsn, directive.get('payload', {}),
                               header.get('instance'))
        return response(directive, props)

    except SkillError as e:
        log.warning('Errore skill: %s %s', e.err_type, e.message)
        return error_response(directive, e.err_type, e.message, e.extra)
    except AylaAuthError as e:
        log.error('Login Ayla fallito: %s', e)
        return error_response(directive, 'INTERNAL_ERROR', 'Credenziali Hisense non valide')
    except AylaError as e:
        log.error('Errore cloud Ayla: %s', e)
        return error_response(directive, 'ENDPOINT_UNREACHABLE', 'Cloud Hisense non raggiungibile')
    except Exception as e:  # noqa: BLE001
        log.exception('Errore inatteso')
        return error_response(directive, 'INTERNAL_ERROR', str(e))
