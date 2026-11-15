# Skill Alexa Smart Home per climatizzatori Hisense (modulo AEH-W4B1)

La Lambda parla direttamente con il **cloud Ayla Networks**, lo stesso usato dall'app
Hisense / Hi-Smart Life, e lo espone ad Alexa come climatizzatore.

| Cosa dici | Interfaccia | Proprietà Ayla |
|---|---|---|
| "Alexa, accendi il condizionatore" | PowerController | `t_power` |
| "Alexa, imposta il condizionatore a 24 gradi" / "alza la temperatura" | ThermostatController | `t_temp` |
| "Alexa, metti il condizionatore in raffreddamento / riscaldamento / automatico" | ThermostatController | `t_work_mode` |
| "Alexa, imposta la modalità del condizionatore su deumidificazione / ventilazione" | ModeController `AC.WorkMode` | `t_work_mode` |
| "Alexa, imposta la ventola del condizionatore su alta" / "aumenta la ventola" | ModeController `AC.FanSpeed` | `t_fan_speed` |
| "Alexa, che temperatura c'è in camera?" | TemperatureSensor | `f_temp_in` |

## 1. Lambda

1. Console AWS → regione **eu-west-1 (Irlanda)**: è quella da usare per le skill in italiano (endpoint Europa).
2. Crea una funzione **Python 3.12** chiamata `hisense-alexa`, con timeout **10 s** e memoria 256 MB.
3. Esegui `.\build.ps1` e carica `build\lambda.zip` (*Code → Upload from → .zip*). L'handler è `lambda_function.lambda_handler`.
4. *Configuration → Environment variables*:
   - `AYLA_EMAIL` e `AYLA_PASSWORD`: le credenziali con cui accedi all'app.
   - `AYLA_APP`: dipende dall'app che usi sul telefono (tabella completa nel README di AirCon):
     **Hi-Smart AC** → `oem-eu` (default) · *HiSmart Life* → `hisense-eu` · *WiFi AC* → `mid-eu`.
     Ogni app ha un suo archivio utenti: con il codice sbagliato la password risulta "errata".
   - **Attenzione:** dopo 5 password errate Ayla blocca l'account. Il client fa un solo tentativo
     e, se viene rifiutato, non ritenta più finché la Lambda non viene riavviata.
   - (facoltativo) `TEMP_UNIT`: `C` o `F` se la temperatura risulta sbagliata.
   - (facoltativo) `ALLOWED_USER_ID`: vedi il punto 4.
5. **Prova** con l'evento `test-events/00-debug-dump.json`. Deve restituire i tuoi climatizzatori, con il `dsn` e le proprietà
   grezze (`t_power`, `t_temp`, `t_work_mode`, `f_temp_in`, ...). Se il login fallisce, prova l'altro valore di `AYLA_APP`.
   Poi metti un `dsn` reale negli altri eventi di test e provali.

> Tieni presente che le credenziali sono salvate in chiaro nelle variabili d'ambiente. Per un uso personale va bene;
> in alternativa puoi spostarle in Secrets Manager.

## 2. Skill Smart Home

1. [developer.amazon.com/alexa/console/ask](https://developer.amazon.com/alexa/console/ask) → *Create Skill*: tipo **Smart Home**, lingua **Italiano**.
2. Copia lo **Skill ID**. Nella Lambda vai su *Add trigger → Alexa* (Alexa Smart Home), incolla lo Skill ID e salva.
3. Nella skill, alla voce *Default endpoint*, incolla l'ARN della Lambda e spunta **Europe, India**, mettendo lo stesso ARN.

## 3. Account linking (obbligatorio) con Login with Amazon

Le skill Smart Home richiedono l'OAuth. Per uso personale la soluzione più semplice è usare **Login with Amazon**:

1. [developer.amazon.com/loginwithamazon/console](https://developer.amazon.com/loginwithamazon/console/site/lwa/overview.html) → *Create a New Security Profile*.
2. In *Web Settings* annota Client ID e Client Secret.
3. Nella skill, alla voce **Account Linking**:
   - Authorization URI: `https://www.amazon.com/ap/oa`
   - Access Token URI: `https://api.amazon.com/auth/o2/token`
   - Client ID / Secret: quelli del security profile
   - Scope: `profile:user_id`
   - Authentication scheme: *HTTP Basic*
4. Copia gli **Alexa Redirect URLs** mostrati in fondo alla pagina e incollali in *Allowed Return URLs* del security profile.

## 4. Attivazione

1. App Alexa → *Altro → Skill e giochi → Le tue skill → Sviluppo* → attiva la skill e fai il login Amazon.
2. "Alexa, cerca dispositivi". I climatizzatori prendono il nome impostato nell'app Hisense e puoi rinominarli nell'app Alexa.

**Sicurezza:** la skill resta in fase di *sviluppo*, quindi solo il tuo account può attivarla, e la Lambda accetta
invocazioni solo dal tuo Skill ID. Se vuoi un ulteriore controllo, recupera il tuo `user_id` dai log e impostalo in
`ALLOWED_USER_ID`: ogni richiesta verrà verificata su `api.amazon.com/user/profile`.

## Note e limiti

- **Unità di temperatura:** secondo AirCon, solo alcune app usano i °C nelle API (`CELSIUS_BASED_APPS`); `oem-eu`
  (Hi-Smart AC) usa i °F e la Lambda converte automaticamente. Se il valore di `t_temp` nel dump non torna, usa `TEMP_UNIT`.
  `f_temp_in` a volte arriva in °F anche su modelli europei, e il codice lo converte automaticamente.
- **Range del setpoint:** 16–32 °C. Per modificarlo cambia `RANGE_C` in `lambda_function.py`.
- **Deumidificazione e ventilazione:** il termostato di Alexa non ha queste modalità. Quando sono attive, il termostato
  risulta "automatico", mentre la modalità reale si vede nel controllo "modalità".
- **Stato in tempo reale:** non viene inviato in modo proattivo (niente ChangeReport). L'app Alexa lo aggiorna quando
  apri il dispositivo.
- Le chiavi delle app Hisense e gli endpoint provengono dal progetto [deiger/AirCon](https://github.com/deiger/AirCon)
  (GPL-3.0) e si trovano in `lambda/app_mappings.py`. Si tratta di un'API non ufficiale: Hisense potrebbe cambiarla,
  per esempio con la migrazione a ConnectLife.

## Riferimenti ai log

Usa CloudWatch → gruppo di log `/aws/lambda/hisense-alexa`. Ogni direttiva viene registrata, e il Discovery registra anche
la lista completa dei dispositivi Ayla.
