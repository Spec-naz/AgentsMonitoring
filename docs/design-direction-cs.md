# Návrh: Agents Monitoring jako řídicí centrum

Stav: návrh k diskusi, bez změn běžící aplikace. Podklad: screenshot uživatele a fork Spec-naz/AgentsMonitoring, commit f9b74ee.

## Zjištění

- Tabulka má pevné rozložení, sloupec názvu 21 %, `white-space: nowrap` a skrytý přesah buněk. Dlouhý název může vytlačit následující Telegram ikonu mimo viditelnou oblast.
- Místní konfigurace obsahuje mapování Telegram botů pro CEO_Master i agent_vsehomira_Claude. Příčinu chybějící ikony je třeba potvrdit v DOM a odpovědi `/api/state` běžící stránky; samotná konfigurace nestačí.
- Výchozí Running vychází z `alive` a `needs_login`. Není to potvrzení funkčního doručení odpovědi ani vykonávání úkolu.
- Současný dashboard používá Python standard library, vložené HTML/JS a polling. Redesign nepotřebuje nový framework.

## Vizuální směr

Tmavé řídicí centrum: grafitové pozadí, střídmé azurové akcenty, jemné linky a jasná typografie. Barva signalizuje význam, nikoli náhodné blikání. Zachovat světlý režim a čitelnost bez barev.

Nahoře přehled počtu agentů a problémů, čas posledního úspěšného načtení. Uprostřed karty agentů, vedle nich nebo níže události. Mobil používá jeden sloupec.

Karta: alias Kóďák/Klóďák, plný technický název dostupný bez hoveru, model, samostatné Telegram tlačítko s rezervovaným místem, stav procesu a stáří dat. Session UUID až v detailu s kopírováním. Jasně pojmenované ovládání, restart a stop s ochranou proti omylu.

Pohyb jen jako zpětná vazba na skutečnou změnu: krátké zvýraznění stavu, příchod události, průběh doložené operace. Respektovat prefers-reduced-motion, navigaci klávesnicí a viditelný focus. Žádná simulovaná telemetrie.

## Etapy

1. Opravit dlouhé názvy a Telegram ovládání; karty, responzivita, detaily, přístupnost a stale/error zobrazení. Použít stávající data. Úspěch: oba názvy i Telegram ovládání jsou dostupné na mobilu i desktopu; výpadek načítání nezůstává jako čerstvý zelený stav.
2. Dodat skutečná měření aktivity a zdraví bridge. Oddělit proces, agentovu aktivitu a doručování. Hodnoty bez důkazu jsou Neznámé. Kontrolní odesílání zpráv není součástí pasivního měření.
3. Sdílené úkoly: ID, zadání, vlastník, repozitář/větev, výstup, kritéria dokončení, reviewer a stav. Předání vyžaduje potvrzení přijetí, historii a ochranu proti duplicitnímu zpracování. Začít ručním přiřazováním; automatickou delegaci až po ověření protokolu.

## Funkce k prioritizaci

- Události a konkrétní diagnostika: bridge konflikt, stará měření, chybějící přihlášení.
- Review před dokončením: druhý agent zaznamená důkazy, rizika a výsledek kontroly.
- Viditelnost souběhu: kdo pracuje v jaké větvi/pracovní kopii. Samotné zobrazení nepředstavuje zámek souborů.
- Kontext, limity a náklady pouze tam, kde existuje zdroj dat; odhad výslovně označit a nepředstírat přesnost předplatného.

## Kritický pohled

Vizuální redesign je přiměřený. Vytvářet současně nový orchestrátor agentů by byl výrazně větší projekt. Nejdřív dokončit jednu srozumitelnou a ověřitelnou etapu. Ticho není chyba: agent může legitimně čekat. Automatický restart podle nečinnosti nebo nekonečné vzájemné zadávání úkolů by zvyšovaly riziko ztráty práce a spotřebu.

Pracovní pravidlo spolupráce: jeden vlastník úkolu, druhý reviewer; implementace ve vlastních větvích/pracovních kopiích; předání obsahuje výsledek, ověření, omezení a další krok. Nesouhlas se zapisuje s důvodem. Dokončeno znamená ověřený výsledek, nikoli pouze sebejisté hlášení agenta.

## Nezávislá revize Claudem

Claude posoudil zadání a výřez kódu v samostatném CLI spuštění. Doporučil oddělit jméno od Telegram ovládání, nepředstírat working/idle pomocí CPU nebo změn výstupu, ponechat polling a odložit předávání úkolů i náklady mimo první verzi. Výslovně označil sdílené úkoly za nový úkolový systém, nikoli kosmetickou úpravu monitoru.

Omezení revize: neviděl screenshot ani celý soubor. V jeho odpovědi je hypotéza o výchozím `overflow:visible`; ta pro tento soubor neplatí, protože skutečné CSS na řádku 86 obsahuje `.agent-rows td{overflow:hidden}`. Návrh opravy oddělením ovladačů zůstává použitelný. Jeho pochybnost o významu `age` není ověřené zjištění chyby. Žádné tvrzení druhého agenta se nepřebírá bez kontroly podkladů.
