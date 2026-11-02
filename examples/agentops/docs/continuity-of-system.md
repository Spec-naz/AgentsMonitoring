# Continuity of system — společný návrh, první ověřitelný krok

Kóďák, Klóďák a Hermík · 2026-10-31. Toto je návrh a nácvik datové obnovy,
nikoli prohlášení, že je celá infrastruktura už přenositelná bez zásahu.

## Co má přežít změnu serveru

Kontinuita znamená obnovit práci, její historii, vlastníky a bezpečný provoz.
Neslibuje pokračování stejného procesu ani přesun živé tmux obrazovky.

| Vrstva | Co zachovat | Jak obnovovat |
|---|---|---|
| Kód | Přesné Git commity aplikací, verzované pluginy, šablony služeb | Čistý checkout/export; nová instalace odpovídající architektuře |
| Provozní data | Úkoly, předání, audit, artefakty, necommitnutá práce, historie monitoringu | Konzistentní snapshoty, schéma, manifest a kontrolní součty |
| Doručování | Telegram offsety, durable inbox/outbox, zpracované update ID, nejasné placené tahy | Obnovit stav před spuštěním konzumentů; duplicity a nejasné výsledky řešit výslovně |
| Identita | Účty poskytovatelů, bot tokeny, tunel, OAuth, šifrovací klíče | Samostatný šifrovaný balík nebo nové přihlášení; ne veřejný Git |
| Externí služby | Base44, modely, Telegram, Cloudflare, DNS, Access | Zvláštní inventář účtů, vlastníků a možností obnovy; nejsou zálohou VPS |

MVP exportuje jen výslovně vybrané lokální kořeny a online snapshot databáze
úkolů. Neexportuje automaticky přihlášení, živé Telegram fronty ani celý HOME.
Historie a pracovní dokumenty mohou být citlivé i bez souboru nazvaného token.

## Reálné cíle a doporučená varianta

Výchozí návrh cílů: **RPO 60 minut, RTO 4 hodiny** při připraveném náhradním
Linuxu. Nejsou naměřené ani zaručené. RPO je možné ztracené období dat;
RTO čas k obnovení použitelného provozu. Lokální nácvik extrakce dat není RTO.

Nyní doporučujeme **cold standby a ověřený ruční restore**, potom připravený
warm standby, pokud bude požadavek na rychlejší návrat a rozpočet. Klóďák volil
cold-first; Hermík navrhl warm standby kvůli kratšímu výpadku. Shodujeme se na
ručním přepnutí a jediném aktivním vykonavateli. Active-active, vlastní distribuovaný
konsenzus a automatický failover jsou pro současnou velikost zbytečně složité.

## Kam lze řešení přenést

| Cíl | Praktická cesta | Co zatím není ověřené |
|---|---|---|
| Jiný VPS/cloud | Čistý Linux, nové binárky, data a služby ze šablon | Přihlášení, tunel a celý průchod musí projít testem |
| On-prem Linux | Stejný datový postup; odchozí tunel a vlastní napájení | Síťová politika, dostupnost úložiště, zálohy mimo objekt |
| Domácí PC | Dedikovaný Linux nebo Linux VM; nastavit zákaz spánku dle potřeby | Výpadky napájení/internetu a spánek nedají stejnou dostupnost jako VPS |
| Windows/macOS | Nejprve Linux VM jako společný provozní základ | Nativní systemd/tmux/sandbox a přihlášení nejsou drop-in port |
| ARM místo x86 | Přenést kód a data, znovu instalovat podporované binárky | Nativní moduly, venv, sandboxy a konkrétní poskytovatelé vyžadují test |

Cesty nesmějí být navždy svázané se starým HOME nebo IP. Přenáší se logické
názvy rolí a verzované šablony s proměnnými, ne cizí binárky a aktivní plánovače.
Docker může později sjednotit část prostředí; sám neřeší identity, licence,
desktopové přihlášení ani ochranu před dvojím vykonáváním.

## Zálohy a bezpečná identita

Po každém významném předání udělat checkpoint úkolu a artefaktu; pravidelně
zálohovat provozní data konzistentně, ideálně mimo poskytovatele hlavního serveru.
SQLite zálohovat online backup API, ne náhodnou kopií hlavního souboru bez WAL.

Produkční obnovovací balík má být šifrovaný zavedeným nástrojem (například age),
s obnovovacím klíčem uloženým mimo zálohovaný server. Implementace MVP žádnou
vlastní kryptografii nevytváří a požadavek přibalit tajemství odmítne. Od AgentOps
0.2 umí datový export šifrovat zavedeným age a ověřit izolované dešifrování;
otestované jsou dočasné identity, nikoli produkční obnovovací klíč uživatele.
Off-host úložiště a přenos ještě nejsou nakonfigurované; lokální archiv není DR záloha.

OAuth či zařízení-vázaná přihlášení nemusí být přenositelná. Přenos tokenu není
garance platného přihlášení. Při podezření na kompromitaci tokeny rotovat a obnovit
přístupy, nekopírovat automaticky staré identity. Zálohovací klíč nesmí být jen
na stroji, jehož ztrátu řešíme.

## Izolovaný nácvik — bez skutečných akcí

1. Zvolit konkrétní verzi kódu a dat; hash archivu uložit odděleně.
2. Obnovit do nového prázdného staging adresáře. Žádné tokeny v prostředí testu.
3. Ověřit manifest, hash každého souboru, počty úkolů/událostí a SQLite integritu.
4. Neinstalovat cron, timery ani boty. Nespouštět webhooky, getUpdates ani placené tahy.
5. Zkontrolovat, že chybějící a vyloučené soubory jsou výslovně uvedené.
6. Zapsat důkaz a dobu nácviku; teprve samostatný test na druhém hostiteli ověří
   přihlášení a provoz. Není dovoleno označit extrakci ZIPu za hotový failover.

## Přepnutí a návrat

1. Připravit nový host pasivně. Záloha může obsahovat nevyřízenou práci; nic z ní
   automaticky nevykonávat. Zkontrolovat všechny cron i systemd spouštěče.
2. Pozastavit příjem práce na původním hostiteli. Dokončit nebo označit nejisté
   externí operace, pořídit finální konzistentní checkpoint.
3. **Prokazatelně odstavit původní konzumenty a vykonavatele externích akcí.**
   Lokální soubor active=true, heartbeat ani expirovaný lease nejsou oplocení
   dvou oddělených serverů. Pokud původní server nelze odstavit či odebrat jeho
   oprávnění u poskytovatele, nepřebírat placené operace automaticky.
4. Přenést finální stav a identity. Telegram má pro každého bota jednoho určeného
   konzumenta. Nativní Base44 Telegram bot zůstává spravovaný v Base44; nepřebírat jej
   dalším lokálním pollerem. Nejasný placený tah ověřit, nikoli slepě zopakovat.
5. Nový host spouštět postupně: lokální kontrola → tunel a přihlášení → zpráva
   přes bota → jeden neplacený test úkolu → teprve skutečné automatizace.
6. Starý host ponechat dostupný pro kontrolu, ale bez aktivních konzumentů.
   Při rollbacku nejprve odstavit nové vykonavatele a promítnout mezitím dokončené
   akce. Spuštění staré databáze bez reconciliation může duplikovat zprávy i platby.

## Oprávnění a náklady

Jeden Unix účet znamená společný přístup k souborům a procesům. Actor v ledgeru
je deklarace, nikoli ověřená identita. UI ani append-only události toto nezmění.
Priorita dalšího kroku je omezit tokeny u poskytovatelů podle konkrétního účelu;
oddělené Unix účty a testované sandboxy jsou navazující migrace, ne hotová vlastnost.

Náklady nyní zapisujeme odděleně jako skutečné/odhadované a podle měny. Chybějící
údaj je neznámý, ne nula. Automatické billing konektory nejsou zapojené.

## Přijetí další etapy

Za přenositelný systém budeme řešení považovat až po obnově na druhém čistém
Linuxu s ověřeným kontextem práce, zprávou tam i zpět, zachovaným doručovacím
stavem a žádným dvojím vykonáním. Pak změříme RPO/RTO a rozhodneme o warm standby.

## Ověřené technické podklady

Konzistentní snapshot živé databáze vychází z [SQLite Online Backup API](https://www.sqlite.org/backup.html).
Vzájemné omezení polling/webhook a potvrzování offsetů popisuje [Telegram Bot API — getUpdates](https://core.telegram.org/bots/api#getupdates).
Jediný aktivní host, oplocení vykonavatelů a naše provozní cíle jsou návrh této
architektury; tyto dokumenty samy negarantují náš failover ani obnovu účtů.
