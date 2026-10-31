# Jednorázové přihlášení serveru k OneDrivu

rclone je nástroj pro automatické přenosy souborů. Má vlastní přihlášení k Microsoftu;
připojení aplikace v chatu nelze bezpečně převzít do serverového plánovače.

V Termius na Macu vytvořit Local port forwarding přes již uložené spojení s VPS:
Local port53682, destination host127.0.0.1, destination port53682. Tunel zapnout.
Tím se dočasná přihlašovací stránka dostupná jen uvnitř VPS zpřístupní jen lokálně
na Macu. Port se neotevírá do internetu a dashboard/tunel se nepřenastavuje.

V SSH terminálu Termius spustit `~/.local/bin/agentbackup-connect`.
Na otázku zda použít prohlížeč odpovědět y. Zobrazený odkaz
http://127.0.0.1:53682/auth?... otevřít v prohlížeči Macu a přihlásit správný účet
Microsoftu. Žádný token ani heslo neposílat do chatu. Pokud přístup zakáže správce,
přenášení se nedokončí a plán zůstává pouze lokální.

Požadované scopes: Files.ReadWrite offline_access. Microsoft potvrzení může
povolovat čtení/zápis souborů účtu, ne jen jediné zálohovací složky. Nastavení
rclone root_folder_id omezuje chování programu, není omezením samotného tokenu.
Nežádáme Sites.Read.All ani vyhledávání ostatních SharePoint webů.

Skript po přihlášení udělá novou šifrovanou zálohu, nahraje ji a stáhne pro
kontrolu SHA256. Teprve po úspěchu přepne hodinový timer z lokálního režimu
na OneDrive. Na konci vypíše HOTOVO. Při selhání se služba nepřepne.
Po dokončení lze dočasný Termius port forwarding vypnout.

Rclone config s OAuth tokenem je privátní600 v ~/.config/agentbackup/rclone.conf.
Nevypisovat jej, neposílat jej do chatu a necommitovat. Privátní age klíč zůstává
na Macu. OAuth přístup lze později odvolat v Microsoft účtu.
