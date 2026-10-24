# Pászuk-kommentár kérdés-térkép

## Cél

Egy olyan eszköz, ami egy Tórá-pászukhoz tartozó klasszikus kommentárokat **a kérdések szerint** rendszerezi MI segítségével. A Tóra öt könyvének bármelyik versére működik (pl. `"Genesis 17:17"`, `"Exodus 3:14"`, `"Deuteronomy 6:4"`): a héber verset és a kommentárokat a Sefariáról tölti le.

> וַיִּפֹּל אַבְרָהָם עַל־פָּנָיו וַיִּצְחָק וַיֹּאמֶר בְּלִבּוֹ הַלְּבֶן מֵאָה־שָׁנָה יִוָּלֵד וְאִם־שָׂרָה הֲבַת־תִּשְׁעִים שָׁנָה תֵּלֵד

### Eredeti kérés

A cél: minden pászukhoz a kérdések szerint rendszerezni AI-val a kommentárokat. Az eredeti kérés az első vizsgált pászukra (Genesis 17:17) szólt:

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

A teljes kimenet futásonként: `out/<Könyv.fej.vers>/result-<tag>.md` és `.json` (a `<tag>` alapból a modell neve), pl. `out/Genesis.17.17/result-openai-luna.md`. Az összehasonlítóhoz lásd a `viewer.py`-t.

## Hogyan működik

```
vers ──sefaria.py: letöltés + szegmentálás──▶ héber vers, szegmensek, kommentárok
     ──clean.py──▶ tisztított szövegek ──kinyerés (MI)──▶ kérdések
     ──ellenőrzés/szűrés──▶ összevonás horgonyonként (MI) ──▶ nested lista
```

1. **Tisztítás** (modell nélkül): a Sefaria-válaszból eltávolítja a HTML-t, a lábjegyzeteket és (alapból) a nikudot. Magyarázónként egy nyelvet tart meg.
2. **Kinyerés** (olcsóbb modell): magyarázónként egy hívás. Listázza, hogy a magyarázó milyen kérdésekre válaszol (explicit és implicit), kiválasztja a horgonyt (`S1..Sn` a vers szegmensei, vagy `CTX`), és szó szerinti idézetet ad.
3. **Ellenőrzés:** az idézetet visszakeresi a magyarázó szövegében (a ⚠ jelöli az igazolatlant), kiszűri az ismétlődést, és egységesíti a neveket (`NAME_FIXES`).
4. **Összevonás** (erősebb modell): horgonyonként párhuzamosan, az azonos kérdést feltevő sorokat egy klaszterbe vonja.
5. **Megjelenítés:** a nested lista Markdown és JSON formában.

Az előzetes eredmény a hűségre épül: a modell csak azt jelentheti, amit a magyarázó mond, idézettel alátámasztva.

## Fájlok

| Fájl | Szerep |
|---|---|
| [sefaria.py](sefaria.py) | Hivatkozás értelmezése, a héber vers és a kommentárok letöltése (gyorsítótárral), a vers szegmentálása. |
| `segments/` | Opcionális kézi szegmentálás versenként (`segments/Genesis.17.17.txt`, soronként egy szegmens). |
| [clean.py](clean.py) | Az 1. lépés: a Sefaria `links` kimenetének tisztítása. Önállóan is futtatható. |
| [pipeline.py](pipeline.py) | A teljes folyamat (kinyerés, ellenőrzés, összevonás, kimenet). A promptok itt vannak. |
| [benchmark.py](benchmark.py) | Különböző MI-modellek/szolgáltatók összemérése ugyanazzal a prompttal. |
| [llm.py](llm.py) | Közös modellregiszter és hívó réteg (Anthropic, OpenAI, Google), árak, költségszámítás. |
| [viewer.py](viewer.py) | Az `out/<vers>/result-*.json` legfrissebb fájljából versenként önálló `kerdes-terkep.html`-t készít (kibontható listák, gépelés közben frissülő keresési számlálók; a kérdés találatakor a válaszok is láthatók; a magyarázók neve új ablakban a Sefaria forrásszövegére linkel). |
| [env.py](env.py) | Minimális `.env` betöltő. |
| `.env.example` | Sablon az API-kulcsokhoz. A saját `.env` gitignore-olt. |
| `out/<Könyv.fej.vers>/` | Versenként: `verse.json`, `links.json` (letöltés-gyorsítótár), `result-<tag>.md/.json`, `<tag>/` köztes fájlok, `kerdes-terkep.html`, `bench/`. |

## Telepítés

```
pip install -r requirements.txt
```

## API-kulcsok

A kulcsokat a projekt mappájában lévő `.env` fájlba írd (a fájl a `.gitignore` miatt nem kerül git-be):

```
cp .env.example .env
# majd szerkeszd a .env-et:
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
```

- A `pipeline.py` és a `benchmark.py` automatikusan betölti ([env.py](env.py), külső csomag nélkül).
- Csak azoknak a cégeknek a kulcsa kell, amiknek a modelljeit éppen használod (pipeline: `--extract-model`/`--merge-model`; benchmark: `--only`).
- Egy már beállított környezeti változó (`export ...`) elsőbbséget élvez a `.env`-vel szemben.
- Ha git-be került már kulcs, forgasd el (rotáld) a szolgáltatónál.

## Használat

### clean.py

```
python clean.py links.json --out cleaned.json [--lang auto|he|en] [--keep-nikud] [--anchor "Genesis 17:17"]
```

### pipeline.py

```
python pipeline.py "Genesis 17:17" --dry-run   # letölt + tisztít, nem hív modellt
python pipeline.py "Exodus 3:14"               # alap: OpenAI gpt-6-luna kinyerésre és összevonásra (kell: OPENAI_API_KEY)
python pipeline.py "Deuteronomy 6:4" --extract-model haiku --merge-model haiku --tag anthropic-haiku   # Anthropic alternatíva
python pipeline.py "Genesis 17:17" --extract-model haiku --merge-model sonnet --tag anthropic-haiku-sonnet   # a régi, drágább felállás
python viewer.py "Genesis 17:17"               # out/Genesis.17.17/kerdes-terkep.html (hivatkozás nélkül: minden versre)
```

A modellnevek az [llm.py](llm.py) `MODELS` regiszteréből valók (`python benchmark.py "Genesis 17:17" --list`). A futás a `result-<tag>.json` `meta` mezőjébe menti a költséget, tokent és időt, amit a `viewer.py` kirajzol (a végösszeggel együtt).

| Kapcsoló | Jelentés |
|---|---|
| `ref` (első argumentum) | a vers: könyv (angolul vagy átírással: Genesis/Bereshit, Exodus/Shemot, Leviticus/Vayikra, Numbers/Bamidbar, Deuteronomy/Devarim), fejezet, vers. Egyetlen vers. |
| `--out` | kimeneti alapmappa (alap: `out`); a vers az `out/<Könyv.fej.vers>/` alá kerül |
| `--links-json` | helyi kommentár-JSON (Sefaria `/api/links` formátum) a letöltés helyett |
| `--refresh` | a vers és a kommentárok újraletöltése (alapból a gyorsítótár van használatban) |
| `--segments-file`, `--max-segments` | kézi szegmentálás fájlból, illetve az automatikus szegmentálás felső határa (alap: 8) |
| `--lang`, `--keep-nikud` | mint a `clean.py`-nál |
| `--extract-model`, `--merge-model` | a két lépés modellje (alap: mindkettő `gpt-6-luna`) |
| `--tag` | a futás neve, ez kerül a fájlnevekbe (alap: a modell neve) |
| `--concurrency` | párhuzamos kinyerések száma (alap: 40) |
| `--extract-max-tokens`, `--merge-max-tokens` | kimeneti tokenkeret |
| `--force` | a gyorsítótárazott kinyerést is újrafuttatja |

A kinyerés magyarázónként gyorsítótárazott (`out/<tag>/extract/`). Újrafutás csak akkor hív API-t, ha a prompt, a modell vagy a szöveg változott. A futás végén kiírja az időt, a tokenszámot és a becsült költséget. Az árak az [llm.py](llm.py)-ban vannak, ellenőrizd őket.

A nevek írásmódját a `NAMES_HINT` és a `NAME_FIXES` állítja be.

**Szegmentálás:** a verset a kantillációs jelek (taamim) erőssége szerint bontja szegmensekre (`S1..Sn`, legfeljebb 8, legalább 3, ha a vers megengedi), a gyengébb határokat összevonva. Ha ez nem jó, a `segments/<Könyv.fej.vers>.txt` fájlba soronként egy szegmenst írva kézzel felülírható (a szegmensek egymás után fűzve ki kell adják a verset). A Genesis 17:17-hez ilyen kézi felosztás van.

**Megjegyzés:** a nevek egységesítése (`NAME_FIXES`) és a promptok a Teremtés könyvének szereplőire lettek finomhangolva; más könyveknél a magyar átírások nem biztos, hogy egységesek, ezeket a `NAMES_HINT`-ben/`NAME_FIXES`-ben lehet kiegészíteni.

### benchmark.py

Összehasonlítja a modelleket a kinyerésen és az összevonáson, ugyanazzal a prompttal, a magyarázók egy rétegzett mintáján.

```
python benchmark.py "Genesis 17:17" --list
python benchmark.py "Genesis 17:17" --only haiku,gpt-6-luna,gemini-3.8-flash --sample 8     # olcsó próba
python benchmark.py "Genesis 17:17" --sample 25 --judge sonnet               # alapos mérés
python benchmark.py "Genesis 17:17" --models my_models.json                  # saját modelllista
```

| Kapcsoló | Jelentés |
|---|---|
| `--sample` | hány magyarázón fusson (alap: 25) |
| `--only` | csak ezek a modellek (vesszővel elválasztva) |
| `--reference` | referencia-modell a viszonyításhoz (alap: `sonnet`) |
| `--include-optional` | az `optional` jelölésű, drága plafon-modellt (`gpt-6-astra`) is futtatja |
| `--judge` | opcionális, vak LLM-bíró (extra költség; a saját családját előnyben részesítheti) |
| `--merge-groups` | a legnagyobb ennyi horgonycsoporton fut az összevonás (alap: 6), `--skip-merge` kihagyja |
| `--concurrency` | párhuzamos hívás modellenként (alap: 8) |
| `--force` | a gyorsítótárat is újraírja |

A riport: `out/<vers>/bench/report.md`. Méri a késleltetést, a költséget (és a teljes anyagra becsült költséget), a JSON-hibákat, a nem igazolt idézetek arányát, az ismétlődést, az idegen írásjegyeket, a referenciával való egyezést, az összevonás minőségét és (opcionálisan) a bíró pontszámait.

**Fontos:**
- A `DEFAULT_MODELS` a 2026. október 24-i hivatalos árlisták alapján készült. Haiku-osztály: `gpt-6-luna`, `gemini-3.5-flash-lite`, `gemini-3.8-flash`. Sonnet-osztály: `gpt-6.1-sol`, `gemini-4-argon`. Plafon (opcionális): `gpt-6-astra`.
- A Gemini árak a promóciós időszak utáni (magasabb) listaárral szerepelnek (`gemini-4-argon`: 2026.12.1-től, `gemini-3.8-flash`: 2027.1.1-től), az OpenAI és Gemini batch kb. fele ennyi. Frissítsd a `price` mezőt, ha változik (`"price": [bemenet, kimenet]` USD / millió token).
- A gondolkodás (reasoning) mindenhol alacsonyra van állítva (`reasoning_effort` / `thinking_level`), mert a tokenjei kimeneti áron számítanak. Érdemes egy magasabb szinttel is kipróbálni egy modellt `--models` JSON-nal.
- Más szolgáltatót (OpenRouter, DeepSeek, Groq, Mistral stb.) az `openai_compat` providerrel veheted fel (`base_url`, `key_env`). Példa a fájlban kommentben van.
- A referencia viszonyítási pont, nem igazság. A döntés előtt nézz bele néhány kimenetbe (`out/<vers>/bench/<modell>/extract/*.json`), főleg a ⚠-os idézetekbe.

## Bővítés más pászukra

Nem kell kézzel semmit cserélni: a vers, a szegmensek, a promptok és a kimeneti mappa a megadott hivatkozásból épülnek. Csak a Tóra öt könyve támogatott; más könyvekhez (Nevi'im, Ketuvim) a `sefaria.py` `BOOKS` listáját kell bővíteni.
