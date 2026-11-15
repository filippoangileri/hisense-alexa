"""Prova locale della skill sui climatizzatori reali (richiede AYLA_EMAIL, AYLA_PASSWORD, AYLA_APP).

Al posto del DSN si puÃ² usare il nome (tra virgolette se ha spazi): "camera da letto".

Esempi:
  python prova.py lista
  python prova.py temp soggiorno 24
  python prova.py stato  AC000W000000000
  python prova.py accendi AC000W000000000
  python prova.py spegni  AC000W000000000
  python prova.py temp    AC000W000000000 24
  python prova.py modo    AC000W000000000 COOL      (FAN, HEAT, COOL, DRY, AUTO)
  python prova.py ventola AC000W000000000 HIGH      (AUTO, LOWER, LOW, MEDIUM, HIGH, HIGHER)
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lambda'))
import lambda_function as lf  # noqa: E402


def directive(namespace, name, dsn, payload=None, instance=None):
    header = {'namespace': namespace, 'name': name, 'payloadVersion': '3',
              'messageId': 'prova', 'correlationToken': 'prova'}
    if instance:
        header['instance'] = instance
    return {'directive': {'header': header,
                          'endpoint': {'scope': {'type': 'BearerToken', 'token': 'prova'},
                                       'endpointId': dsn},
                          'payload': payload or {}}}


def resolve(target):
    """Accetta un DSN o il nome del condizionatore (es. 'soggiorno', 'camera da letto')."""
    devices = lf.client().devices()
    by_dsn = {d['dsn'].upper(): d['dsn'] for d in devices}
    if target.upper() in by_dsn:
        return by_dsn[target.upper()]
    by_name = {(d.get('product_name') or '').lower(): d['dsn'] for d in devices}
    if target.lower() in by_name:
        return by_name[target.lower()]
    print(f'Condizionatore "{target}" non trovato. Disponibili:')
    for d in devices:
        print(f"  {d['dsn']}  {d.get('product_name')}")
    return None


def main(argv):
    if len(argv) < 2 or argv[1] not in COMMANDS:
        print(__doc__)
        return 1
    cmd, args = argv[1], argv[2:]
    if cmd == 'lista':
        event = {'directive': {'header': {'namespace': 'Alexa.Discovery', 'name': 'Discover',
                                          'payloadVersion': '3', 'messageId': 'prova'},
                               'payload': {}}}
        resp = lf.lambda_handler(event, None)
        for ep in resp['event']['payload'].get('endpoints', []):
            print(f"{ep['endpointId']}  {ep['friendlyName']}")
        if 'endpoints' not in resp['event']['payload']:
            print(json.dumps(resp, indent=2, ensure_ascii=False))
        return 0
    if not args:
        print(__doc__)
        return 1
    args[0] = resolve(args[0])
    if args[0] is None:
        return 1
    try:
        event = COMMANDS[cmd](*args)
    except TypeError:
        print(f'Numero di argomenti errato per "{cmd}".')
        print(__doc__)
        return 1
    resp = lf.lambda_handler(event, None)
    event = resp['event']
    print('Risposta:', event['header']['name'], event['payload'].get('type', ''),
          event['payload'].get('message', ''))
    for p in resp.get('context', {}).get('properties', []):
        label = f"{p['namespace'].split('.')[-1]}.{p['name']}"
        if p.get('instance'):
            label += f" ({p['instance']})"
        print(f'  {label:45} {p["value"]}')
    return 0


COMMANDS = {
    'lista': None,
    'stato': lambda dsn: directive('Alexa', 'ReportState', dsn),
    'accendi': lambda dsn: directive('Alexa.PowerController', 'TurnOn', dsn),
    'spegni': lambda dsn: directive('Alexa.PowerController', 'TurnOff', dsn),
    'temp': lambda dsn, c: directive('Alexa.ThermostatController', 'SetTargetTemperature', dsn,
                                     {'targetSetpoint': {'value': float(c), 'scale': 'CELSIUS'}}),
    'modo': lambda dsn, m: directive('Alexa.ModeController', 'SetMode', dsn,
                                     {'mode': f'WorkMode.{m.upper()}'}, lf.MODE_INSTANCE),
    'ventola': lambda dsn, v: directive('Alexa.ModeController', 'SetMode', dsn,
                                        {'mode': f'FanSpeed.{v.upper()}'}, lf.FAN_INSTANCE),
}

if __name__ == '__main__':
    sys.exit(main(sys.argv))
