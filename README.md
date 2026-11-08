# Fotodokumentace Blanska – archiv z Trella

Záloha Trello boardu [Fotodokumentace](https://trello.com/b/2Fa6uRao/fotodokumentace): karty s popisy a všechny fotky z příloh.

**Galerie:** <https://sauness.github.io/trello-fotodokumentace/>  
**Archiv:** [`archive/README.md`](archive/README.md)

Galerii staví `scripts/build_site.py` (šablona `scripts/site/index.html`) a workflow
„Galerie (GitHub Pages)“ ji nasadí po každém exportu z Trella i po změně skriptů.

## Struktura

```
archive/
  README.md                         přehled listů
  01-centrum-blanska/
    README.md                       seznam karet v listu
    pruvod-rozmitalova-1955-ziKE2WBf/
      README.md                     popis karty + galerie
      01-fb-img-1683091826555.jpg   přílohy
```

Složka karty končí jejím Trello shortLinkem, takže přejmenování karty v Trellu
nevytvoří duplikát. Karty, listy a přílohy smazané v Trellu se z archivu odstraní.

## Nastavení (jednorázově)

1. Vytvoř Trello API klíč: <https://trello.com/power-ups/admin> → **New** (integrace,
   stačí libovolný název a workspace) → záložka **API key** → **Generate a new API key**.
2. Na stejné stránce klikni na odkaz **Token** (vpravo od klíče) a povol přístup
   – dostaneš token.
3. V tomto repu: **Settings → Secrets and variables → Actions → New repository secret**
   - `TRELLO_API_KEY` = klíč
   - `TRELLO_TOKEN` = token
4. **Actions → Trello export → Run workflow.**

Pak workflow běží automaticky každé pondělí a commitne jen změny.
Jiný board nastavíš proměnnou `TRELLO_BOARD` (Settings → Variables), výchozí je `2Fa6uRao`.

## Ruční spuštění lokálně

```bash
export TRELLO_API_KEY=... TRELLO_TOKEN=...
python scripts/trello_export.py --board 2Fa6uRao --out archive
```

Skript používá jen standardní knihovnu Pythonu 3.9+. Opakované spuštění stahuje
pouze nové nebo změněné přílohy.
