# Pászuk-kommentár kérdés-térkép

## Cél

Egy olyan eszköz, ami egy Tórá-pászukhoz tartozó klasszikus kommentárokat **a kérdések szerint** rendszerezi MI segítségével. Egyelőre egyetlen pászukra készült: **Bereshit (Genesis) 17:17**.

> וַיִּפֹּל אַבְרָהָם עַל־פָּנָיו וַיִּצְחָק וַיֹּאמֶר בְּלִבּוֹ הַלְּבֶן מֵאָה־שָׁנָה יִוָּלֵד וְאִם־שָׂרָה הֲבַת־תִּשְׁעִים שָׁנָה תֵּלֵד

### Eredeti kérés

A cél: minden pászukhoz a kérdések szerint rendszerezni AI-val a kommentárokat. Egyelőre erre a konkrét pászukra:

- Végig kell menni az összes kommentár kérdésein, és csoportosítva leírni őket aszerint, hogy a pászuk melyik szavaihoz tartoznak.
- Külön csoportba kerülnek azok a kérdések, amik nem konkrét szavakhoz tartoznak (nem a nyelvezetre kérdeznek, hanem a kontextus szerinti témára): *dibur hámátchil*-hoz nem köthető kérdések.
- Az implicit kérdéseket is össze kell írni: amik nincsenek kiírva, de a magyarázó válaszol rájuk.

A kimenet egy beágyazott (nested) lista:

1. **szint:** először a nem konkrét szavakhoz kötött kérdések, majd a pászuk szavai. Mellettük zárójelben, hogy hány kérdés tartozik oda.
2. **szint:** a konkrét kérdések magyarul, mellettük, hogy hány magyarázó adresszálja őket.
3. **szint:** maguk a válaszok egy rövid magyar mondatban, zárójelben a magyarázó nevével.

### Példa a kimenetre

```
- **Nem konkrét szóhoz kötött, kontextuális kérdések (12)**
    - Kételkedett-e Ávráhám Isten ígéretében? (5)
        - Nem kételkedett, csodálkozott/örült. (Rashi)
        - ...
- **וַיִּצְחָק (3)**
    - Az "וַיִּצְחָק" nevetést vagy gúnyt jelent? [implicit] (2)
        - ...
```

A teljes kimenet: `out/result.md` és `out/result.json`.

## Hogyan működik

```
links.json ──clean.py──▶ tisztított szövegek ──kinyerés (MI)──▶ kérdések
          ──ellenőrzés/szűrés──▶ összevonás horgonyonként (MI) ──▶ nested lista
```

1. **Tisztítás** (modell nélkül): a Sefaria-válaszból eltávolítja a HTML-t, a lábjegyzeteket és (alapból) a nikudot. Magyarázónként egy nyelvet tart meg.
2. **Kinyerés** (olcsóbb modell): magyarázónként egy hívás. Listázza, hogy a magyarázó milyen kérdésekre válaszol (explicit és implicit), kiválasztja a horgonyt (`S1..S6` a pászuk szegmensei, vagy `CTX`), és szó szerinti idézetet ad.
3. **Ellenőrzés:** az idézetet visszakeresi a magyarázó szövegében (a ⚠ jelöli az igazolatlant), kiszűri az ismétlődést, és egységesíti a neveket (`NAME_FIXES`).
4. **Összevonás** (erősebb modell): horgonyonként párhuzamosan, az azonos kérdést feltevő sorokat egy klaszterbe vonja.
5. **Megjelenítés:** a nested lista Markdown és JSON formában.

Az előzetes eredmény a hűségre épül: a modell csak azt jelentheti, amit a magyarázó mond, idézettel alátámasztva.

## Fájlok

| Fájl | Szerep |
|---|---|
| [clean.py](clean.py) | Az 1. lépés: a Sefaria `links` kimenetének tisztítása. Önállóan is futtatható. |
| [pipeline.py](pipeline.py) | A teljes folyamat (kinyerés, ellenőrzés, összevonás, kimenet). A promptok itt vannak. |
| [benchmark.py](benchmark.py) | Különböző MI-modellek/szolgáltatók összemérése ugyanazzal a prompttal. |
| [links.json](links.json) | Bemenet: a Sefaria `/api/links/Genesis.17.17?with_text=1` válasza. |
| `out/` | Kimenet és gyorsítótár (`cleaned.json`, `items.json`, `clusters.json`, `result.*`, `extract/`, `bench/`). |

## Telepítés

```
pip install anthropic            # a pipeline-hoz
pip install openai google-genai  # csak a benchmarkhoz
```

API-kulcsok környezeti változóként: `ANTHROPIC_API_KEY`, a benchmarkhoz még `OPENAI_API_KEY` és `GEMINI_API_KEY` (vagy `GOOGLE_API_KEY`).

## Használat

### clean.py

```
python clean.py links.json --out cleaned.json [--lang auto|he|en] [--keep-nikud]
```

### pipeline.py

```
python pipeline.py links.json --dry-run   # csak tisztít, nem hív API-t
python pipeline.py links.json             # teljes futás
```

| Kapcsoló | Jelentés |
|---|---|
| `--out` | kimeneti mappa (alap: `out`) |
| `--lang`, `--keep-nikud` | mint a `clean.py`-nál |
| `--extract-model`, `--merge-model` | a két lépés modellje (alap: `claude-haiku-5-5`, `claude-sonnet-5-5`) |
| `--concurrency` | párhuzamos kinyerések száma (alap: 40) |
| `--extract-max-tokens`, `--merge-max-tokens` | kimeneti tokenkeret |
| `--force` | a gyorsítótárazott kinyerést is újrafuttatja |

A kinyerés magyarázónként gyorsítótárazott (`out/extract/`). Újrafutás csak akkor hív API-t, ha a prompt, a modell vagy a szöveg változott. A futás végén kiírja az időt, a tokenszámot és a becsült költséget. A `PRICES` az árakat tartalmazza, ellenőrizd őket.

A nevek írásmódját a `NAMES_HINT` és a `NAME_FIXES` állítja be, a pászuk szegmensei a `SEGMENTS` szótárban vannak.

### benchmark.py

Összehasonlítja a modelleket a kinyerésen és az összevonáson, ugyanazzal a prompttal, a magyarázók egy rétegzett mintáján.

```
python benchmark.py links.json --list
python benchmark.py links.json --only haiku,gemini-flash --sample 8     # olcsó próba
python benchmark.py links.json --sample 25 --judge sonnet               # alapos mérés
python benchmark.py links.json --models my_models.json                  # saját modelllista
```

| Kapcsoló | Jelentés |
|---|---|
| `--sample` | hány magyarázón fusson (alap: 25) |
| `--only` | csak ezek a modellek (vesszővel elválasztva) |
| `--reference` | referencia-modell a viszonyításhoz (alap: `sonnet`) |
| `--judge` | opcionális, vak LLM-bíró (extra költség; a saját családját előnyben részesítheti) |
| `--merge-groups` | a legnagyobb ennyi horgonycsoporton fut az összevonás (alap: 6), `--skip-merge` kihagyja |
| `--concurrency` | párhuzamos hívás modellenként (alap: 8) |
| `--force` | a gyorsítótárat is újraírja |

A riport: `out/bench/report.md`. Méri a késleltetést, a költséget (és a teljes anyagra becsült költséget), a JSON-hibákat, a nem igazolt idézetek arányát, az ismétlődést, az idegen írásjegyeket, a referenciával való egyezést, az összevonás minőségét és (opcionálisan) a bíró pontszámait.

**Fontos:**
- A `DEFAULT_MODELS` Google és OpenAI azonosítói csak kiindulópontok, a Google és OpenAI áraknál `None` áll, amíg nem adod meg őket (`"price": [bemenet, kimenet]` USD / millió token). Ellenőrizd őket.
- Más szolgáltatót (OpenRouter, DeepSeek, Groq, Mistral stb.) az `openai_compat` providerrel veheted fel (`base_url`, `key_env`). Példa a fájlban kommentben van.
- A referencia viszonyítási pont, nem igazság. A döntés előtt nézz bele néhány kimenetbe (`out/bench/<modell>/extract/*.json`), főleg a ⚠-os idézetekbe.

## Bővítés más pászukra

A pászuk szövege (`VERSE`), a szegmensek (`SEGMENTS`), a hivatkozási horgony (`clean_links`) és a nevek (`NAMES_HINT`, `NAME_FIXES`) most erre az egy pászukra vannak beállítva. Egy új pászukhoz ezeket kell cserélni a `pipeline.py`-ban, és új `links.json`-t kell letölteni a Sefariáról.
