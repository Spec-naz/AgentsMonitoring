#!/bin/bash
# Run in the user's SSH terminal after configuring local port forwarding 53682.
set -euo pipefail
umask 077
cfg="$HOME/.config/agentbackup/settings.json"
rclone="$HOME/.local/opt/agentbackup/v1.75.2/rclone"
rc="$HOME/.config/agentbackup/rclone.conf"
printf 'Prihlaseni Microsoftu: pouzijte stejny ucet jako pripojeny OneDrive.\n'
printf 'Odkaz 127.0.0.1:53682 otevrete na Macu az po zapnuti SSH presmerovani tohoto portu.\n'
printf 'Pokud se zepta na pouziti prohlizece, odpovezte y. Tokeny nikam neposilejte.\n'
BROWSER=/usr/bin/true "$rclone" --config "$rc" config reconnect agentbackup:
chmod 600 "$rc"
printf 'Probiha prvni automaticky prenos a kontrolni stazeni...\n'
/usr/bin/python3 "$HOME/.agentbackup-prod/current/backup.py" --config "$cfg"
# Only enable remote scheduled mode after a real verified roundtrip succeeds.
/usr/bin/python3 - <<'PY'
from pathlib import Path
import json
h=Path.home();state=h/'.local/state/agentbackup';s=json.loads((state/'last-offsite-success.json').read_text())
assert s['offsite_ok']
p=h/'.config/systemd/user/agentbackup.service';text=p.read_text()
text=text.replace(' --local-only','').replace('(local encrypted; OneDrive authorization pending)','(encrypted with verified OneDrive copy)')
p.write_text(text)
o=h/'.config/agentops/onedrive.json';d=json.loads(o.read_text());d.update({'unattended_schedule_enabled':True,'transport':'server-rclone-oauth','first_server_offsite_sha256':s['sha256']});o.write_text(json.dumps(d,indent=2)+'\n');o.chmod(0o600)
PY
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
systemctl --user daemon-reload
systemctl --user enable --now agentbackup.timer
printf '\nHOTOVO: denni sifrovane zalohy s overenym prenosem na OneDrive jsou aktivni.\n'
