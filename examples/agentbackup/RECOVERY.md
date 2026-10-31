# Obnova aplikací — 31. 10. 2026

Tento balík obnovuje vyjmenované aplikace a data, ne celý operační systém.
Zálohy jsou šifrované pro veřejný age klíč Michala. Soukromý klíč je mimo VPS.

## Co je zahrnuto
- Kompletní immutable kód AgentsMon a AgentOps, včetně config.py/identity.py.
- Zdroj sdíleného Mozku a FOCUS OS (bez .env, node_modules, dist a .git).
- Poznámky brain a kód offline sandboxu a tohoto zálohování.
- Konzistentní online SQLite kopie úkolů a historie monitoringu.
- Postgres dump HumanAgentWiki včetně ověření pg_restore --list.
- Tento postup a cesty/verze zdrojů. Přesné vyloučení uvádí manifest.

## Co záměrně není zahrnuto
Živé přihlašovací údaje, bot tokeny, OAuth cache, Cloudflare identita, soukromý
age klíč, kompletní HOME, živé tmux procesy a doručovací fronty Telegram bridge.
Tyto služby vyžadují nové přihlášení a kontrolované přepnutí. Zdrojové soubory
jsou kopírovány jednotlivě, nejde o společný atomický snapshot všech aplikací.
Před nasazením na jiný host ověřit konfiguraci, závislosti a cesty pro daný OS.

## Obnova do samostatné složky
1. Z OneDrive AgentOps-Backups stáhnout archiv .zip.age a odpovídající .json.
   Zaznamenaný SHA256 porovnat i s odděleným důkazem; hash na stejném úložišti
   sám neprokazuje původ. Obnovovací klíč získat z Michalovy bezpečné kopie.
2. Připravit Python3.10+ a ověřený age (viz oficiální age-encryption.org).
   Použít recovery-kit s restore.py a continuity_restore.py; nepouštět cizí
   skripty bez kontroly jejich původu. Na původním VPS je kit v ~/AgentBackup.
3. Spustit:
   python3 restore.py --archive /cesta/zaloha.zip.age --sha256 ZAZNAMENANY_HASH --identity /bezpecne/agentops-recovery-key.txt --age /cesta/age --output /nova/prazdna/slozka
   Nejprve ověří hash, dešifruje v soukromé dočasné složce, pak ověří manifest,
   každý soubor a SQLite integritu. Nic nenahrazuje ani nespouští.
4. Obnovené soubory jsou pod files/<jmeno>. Zkontrolovat úplnost manifestu.
   Postgres obnovovat do NOVÉ prázdné databáze kompatibilní verze příkazem
   pg_restore --exit-on-error --no-owner --no-privileges --dbname NOVA_DB files/recovery/wiki-postgres.dump
   Nepoužívat produkční DB; SQL dump je důvěryhodný jen jako vlastní ověřená záloha.
5. Z obnovy nepouštět boty, keepalive, cron ani tunel. Nejprve izolovaný sandbox
   a test dashboardu s prázdnou konfigurací. Instalace kódu a jeho závislostí
   zůstává samostatným krokem; serverové binárky nejsou přenositelné na Mac/ARM.
6. Produkční konfigurace: nová přihlášení, explicitní kontrola starého hostu,
   zastavení původních konzumentů a teprve potom spuštění nové jediné instance.
   Nespouštět dva Telegram pollery stejného bota. Při návratu nejprve zastavit
   novou instanci a vyřešit mezitím provedené akce; nevracet slepě starou DB.

## Plán, kapacita a návrat
Každou hodinu nový šifrovaný archiv; další přenos jen po serverovém přihlášení
rclone. Upload je kopie, nikdy sync. Po uploadu se archiv stáhne a ověří SHA256.
Lokálně uchovat posledních48, ale staré lokální soubory mazat pouze s dokladem
úspěšného offsite přenosu. Vzdálené soubory se automaticky nemažou. Nastavení
vzdálené retenční politiky následuje po měření skutečného denního růstu.
Soubor last-offsite-success.json je důkaz posledního úspěchu, last-attempt.json
posledního pokusu; existence nového místního souboru neznamená nový OneDrive backup.
Neúspěch přenosu se nezaměňuje za úspěch zálohy mimo server.

Timer se zastaví: systemctl --user disable --now agentbackup.timer
Nezasahuje do běžících aplikací. Plný failover, obnova přihlášení, front a celé
infrastruktury není ověřená. Sandbox na stejném VPS nenahrazuje další host.
