# NT8 Connection Alert Notifier

This setup keeps trading and alerting separate.

The standalone NT8 indicator writes each price/order connection loss to a local TSV
file. A separate Python script reads that queue and sends the alert to
Healthchecks.io. If internet access is down, the Python script retries later.

## Files

- `NT8ConnectionAlertFileLogger.cs` - standalone NT8 indicator that writes connection alerts.
- `nt8_connection_notifier.py` - Healthchecks-only Python notifier.
- `notifier_config.example.json` - example notifier configuration.

## NT8 Indicator

Add `NT8ConnectionAlertFileLogger` to one chart that stays open. It does not submit,
cancel, or modify orders.

Indicator settings:

- `Enable File Alerts` - keep this enabled.
- `Alert Queue File Path` - default:
  `C:\Users\Liikurserv\Documents\NinjaTrader 8\connection_alerts\connection_alert_queue.tsv`
- `Log Price Connection Loss` - writes price-feed connection loss events.
- `Log Order Connection Loss` - writes order-system connection loss events.
- `Log Reconnect Events` - optional, disabled by default.

## Healthchecks Setup

Create a check in Healthchecks.io and copy its ping URL.

The notifier can use Healthchecks in two ways:

- Connection-loss events are sent to the `/fail` endpoint by default.
- Optional heartbeat pings are sent to the normal ping URL, so Healthchecks can
  detect if the PC, internet connection, or Python notifier stops working.

## Python Setup

1. Copy `notifier_config.example.json` to `notifier_config.json`.
2. Set `HEALTHCHECKS_PING_URL` in PowerShell:

```powershell
$env:HEALTHCHECKS_PING_URL = "https://hc-ping.com/your-check-uuid"
```

3. Start the notifier:

```powershell
python nt8_connection_notifier.py --config notifier_config.json
```

To test without running forever:

```powershell
python nt8_connection_notifier.py --config notifier_config.json --once
```

## Optional Heartbeat

To make Healthchecks also monitor whether this Python notifier is still alive, set
`send_heartbeat` to `true` in `notifier_config.json`.

```json
"healthchecks": {
  "enabled": true,
  "ping_url": "env:HEALTHCHECKS_PING_URL",
  "send_failure": true,
  "send_heartbeat": true,
  "heartbeat_seconds": 60,
  "timeout_seconds": 20
}
```

## Retry Behavior

The notifier keeps delivery state in `notifier_state.json`.

If Healthchecks cannot be reached because internet is down, the alert remains unsent
and is retried after connectivity returns.
