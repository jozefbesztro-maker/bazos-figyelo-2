# Bazoš figyelő 2.0

Óránként átnézi a bazos.sk-t Komárno + 60 km körzetében, minden hirdetést összevet ugyanannak a terméknek a szlovákiai medián árával, és e-mailt küld, ha valami jóval olcsóbb a piaci árnál. Este napi összefoglalót is küld.

- **Beállítások** (körzet, kedvezmény-határ, figyelt termékek): a `bazos_figyelo.py` elején. A GitHubon is szerkeszthető (ceruza ikon).
- **Kézi futtatás:** Actions → *Bazos figyelo* → *Run workflow* → `normal`, `proba` (nem küld levelet), `jelentes` (napi összefoglaló most) vagy `teszt-email`.
- **Futások naplója:** Actions fül → bármelyik futás → *figyel* → *Bazoš átnézése*.
- **Piaci napló:** `piaci_naplo.csv` – minden árazott hirdetés, a piaci árral együtt.
- **Leállítás:** Actions → *Bazos figyelo* → `...` → *Disable workflow*.

Titkos beállítások (Settings → Secrets and variables → Actions):
- `GMAIL_USER` – a Gmail-cím, amelyről és amelyre a levelek mennek
- `GMAIL_APP_PASSWORD` – Gmail alkalmazásjelszó
- `EMAIL_TO` – (nem kötelező) ha máshová kéred a leveleket
