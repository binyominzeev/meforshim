"""Sefaria-letöltés és a vers előkészítése (Tóra, bármelyik pászuk).

- parse_ref: "Genesis 17:17", "Exodus.3.14", "Devarim 6:4" ... -> (könyv, fejezet, vers)
- prepare: letölti (és gyorsítótárazza) a héber verset és a kommentárokat, a verset szegmensekre bontja
"""
import html
import json
import re
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

API = "https://www.sefaria.org/api"
ROOT = Path(__file__).parent

BOOKS = {
    "Genesis": ["genesis", "gen", "gn", "bereshit", "bereishit", "berésit", "beresit", "בראשית"],
    "Exodus": ["exodus", "exod", "ex", "shemot", "smot", "smót", "שמות"],
    "Leviticus": ["leviticus", "lev", "lv", "vayikra", "vajikra", "vájikra", "ויקרא"],
    "Numbers": ["numbers", "num", "nu", "bamidbar", "bemidbar", "במדבר"],
    "Deuteronomy": ["deuteronomy", "deut", "dt", "devarim", "dvarim", "דברים"],
}
ALIASES = {alias: book for book, names in BOOKS.items() for alias in names}

TAAMIM = re.compile("[\u0591-\u05AF\u05BD]")  # kantillációs jelek és meteg; a magánhangzók maradnak
NIKUD_ALL = re.compile("[\u0591-\u05C7]")
TAG = re.compile(r"<[^>]+>")

# Az ékezetek (taamim) erőssége: minél nagyobb, annál erősebb szünet a szó után.
STRENGTH = {0x591: 5, 0x592: 4, 0x593: 4, 0x594: 4, 0x595: 4, 0x596: 3, 0x597: 3,
            0x598: 2, 0x599: 2, 0x59A: 2, 0x59B: 2, 0x59C: 1, 0x59D: 1, 0x59E: 1, 0x59F: 1, 0x5A0: 1, 0x5A1: 1}


@dataclass
class Verse:
    ref: str  # kanonikus, pl. "Genesis 17:17"
    he_ref: str
    text: str  # héber, taamimmal
    segments: OrderedDict  # "S1" -> héber szövegrész (taamim nélkül)

    @property
    def slug(self):
        return slugify(self.ref)

    def to_json(self):
        return {"ref": self.ref, "he_ref": self.he_ref, "text": self.text, "segments": dict(self.segments)}


def slugify(ref):
    return re.sub(r"[^A-Za-z0-9]+", ".", ref).strip(".")


def parse_ref(s):
    """Szöveges hivatkozásból (könyv, fejezet, vers). Csak egyetlen vers fogadható el."""
    m = re.match(r"^\s*(.+?)[\s.]+(\d+)\s*[:.\s,]\s*(\d+)\s*$", s)
    if not m:
        raise SystemExit(f"Nem értem a hivatkozást: {s!r}. Példa: \"Genesis 17:17\" vagy \"Deuteronomy 6:4\" "
                         "(egyetlen vers; a Tóra öt könyve).")
    key = re.sub(r"[^\w]", "", m.group(1).lower())
    book = ALIASES.get(key)
    if not book:
        raise SystemExit(f"Ismeretlen könyv: {m.group(1)!r}. Elfogadott: Genesis, Exodus, Leviticus, Numbers, Deuteronomy "
                         "(vagy Bereshit, Shemot, Vayikra, Bamidbar, Devarim).")
    return book, int(m.group(2)), int(m.group(3))


def _get(url, tries=3):
    req = urllib.request.Request(url, headers={"User-Agent": "meforshim/1.0"})
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                msg = json.loads(body).get("error", body)
            except ValueError:
                msg = body[:200]
            if e.code < 500:
                raise SystemExit(f"Sefaria: {msg}")
            err = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError) as e:
            err = str(e)
        if i == tries - 1:
            raise SystemExit(f"Sefaria-letöltés sikertelen ({url}): {err}")
        time.sleep(2 * (i + 1))


def clean_verse(raw):
    s = html.unescape(TAG.sub("", raw))
    s = re.sub(r"\{[^}]*\}", "", s)  # {ס} {פ}: szakaszjelek
    s = s.replace("\u05C0", " ").replace("\u05C3", "")  # paszek, szof pászuk
    return " ".join(s.split())


def strength(word):
    return max((STRENGTH.get(ord(ch), 0) for ch in word), default=0)


def segment(text, max_segments=8, min_words=2, min_segments=3):
    """A verset a kantillációs jelek erőssége szerint bontja: a leggyengébb határokat vonja össze,
    amíg nincs túl sok szegmens és mindegyikben van legalább `min_words` szó (de `min_segments` alá nem megy)."""
    words = text.split()
    chunks, bounds, cur = [], [], []
    for w in words:
        cur.append(w)
        if strength(w):
            chunks.append(cur)
            bounds.append(strength(w))
            cur = []
    if cur:
        chunks.append(cur)
        bounds.append(0)
    bounds = bounds[:len(chunks) - 1]  # a vers végén nincs határ
    while len(chunks) > 1:
        small = [i for i, c in enumerate(chunks) if len(c) < min_words] if len(chunks) > min_segments else []
        if small:
            i = small[0]
            left = bounds[i - 1] if i > 0 else None
            right = bounds[i] if i < len(chunks) - 1 else None
            j = i - 1 if right is None or (left is not None and left <= right) else i
        elif len(chunks) > max_segments:
            j = min(range(len(chunks) - 1), key=lambda k: (bounds[k], len(chunks[k]) + len(chunks[k + 1])))
        else:
            break
        chunks[j] += chunks.pop(j + 1)
        bounds.pop(j)
    return [TAAMIM.sub("", " ".join(c)) for c in chunks]


def _plain(s):
    return " ".join(NIKUD_ALL.sub("", s).replace("־", " ").split())


def load_segments_file(path, text):
    segs = [ln.strip() for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    if _plain(" ".join(segs)) != _plain(text):
        raise SystemExit(f"A(z) {path} szegmensei egymás után fűzve nem adják ki a verset.\n"
                         f"  vers:      {_plain(text)}\n  szegmensek: {_plain(' '.join(segs))}")
    return segs


def prepare(a):
    """Visszaad: (Verse, a kommentárok JSON-ja, a vers mappája). A letöltések a vers mappájában gyorsítótárazódnak."""
    book, ch, v = parse_ref(a.ref)
    dot = f"{book}.{ch}.{v}"
    vdir = Path(a.out) / dot

    vf = vdir / "verse.json"
    if vf.exists() and not a.refresh:
        saved = json.loads(vf.read_text(encoding="utf-8"))
        ref, he_ref, text = saved["ref"], saved["he_ref"], saved["text"]
    else:
        d = _get(f"{API}/v3/texts/{dot}?version=hebrew")
        ver = next((x for x in d.get("versions", []) if isinstance(x.get("text"), str) and x["text"].strip()), None)
        if not ver:
            raise SystemExit(f"A(z) {dot} héber szövege nem érhető el a Sefarián.")
        ref, he_ref, text = d["ref"], d.get("heRef", ""), clean_verse(ver["text"])
        vdir.mkdir(parents=True, exist_ok=True)
        vf.write_text(json.dumps({"ref": ref, "he_ref": he_ref, "text": text}, ensure_ascii=False, indent=1),
                      encoding="utf-8")

    seg_file = a.segments_file or (ROOT / "segments" / f"{slugify(ref)}.txt")
    if a.segments_file and not Path(a.segments_file).exists():
        raise SystemExit(f"Nincs ilyen fájl: {a.segments_file}")
    if Path(seg_file).exists():
        segs = load_segments_file(seg_file, text)
        print(f"Szegmensek: kézi felosztás ({seg_file})")
    else:
        segs = segment(text, a.max_segments)
    verse = Verse(ref, he_ref, text, OrderedDict((f"S{i}", s) for i, s in enumerate(segs, 1)))

    if a.links_json:
        links = json.loads(Path(a.links_json).read_text(encoding="utf-8"))
    else:
        lf = vdir / "links.json"
        if lf.exists() and not a.refresh:
            links = json.loads(lf.read_text(encoding="utf-8"))
        else:
            links = _get(f"{API}/links/{dot}?with_text=1&category=Commentary")
            lf.write_text(json.dumps(links, ensure_ascii=False), encoding="utf-8")
    return verse, links, vdir


def add_args(ap):
    """A közös, versre vonatkozó kapcsolók (pipeline.py, benchmark.py)."""
    ap.add_argument("ref", help='a vers, pl. "Genesis 17:17", "Exodus 3:14", "Deuteronomy 6:4"')
    ap.add_argument("--out", default="out", help="kimeneti alapmappa; a vers az out/<Könyv.fej.vers>/ alá kerül")
    ap.add_argument("--links-json", help="helyi kommentár-JSON a letöltés helyett (Sefaria /api/links formátum)")
    ap.add_argument("--refresh", action="store_true", help="a vers és a kommentárok újraletöltése")
    ap.add_argument("--segments-file", help="kézi szegmentálás: soronként egy szegmens (alap: segments/<vers>.txt, ha van)")
    ap.add_argument("--max-segments", type=int, default=8, help="automatikus szegmentálásnál a szegmensek legnagyobb száma")
