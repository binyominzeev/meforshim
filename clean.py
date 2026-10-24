#!/usr/bin/env python3
"""1. lépés: Sefaria /api/links?with_text=1 válasz -> tisztított, magyarázónkénti szöveg.

Nem használ modellt. Kidobja a HTML-t, a lábjegyzeteket és (alapból) a nikudot,
magyarázónként csak egy nyelvet tart meg. Ettől az input a töredékére esik vissza.
"""
import argparse
import html
import json
import re
from collections import OrderedDict

NIKUD = re.compile(r"[\u0591-\u05C7]")  # nikud + taamim
SUP_MARKER = re.compile(r'<sup class="footnote-marker">.*?</sup>', re.S)
TAG = re.compile(r"<[^>]+>")
SPACES = re.compile(r"[ \t\r\f\v]+")
FOOTNOTE_OPEN = '<i class="footnote">'


def strip_footnotes(s: str) -> str:
    """Törli a lábjegyzet-jelölőket és a (beágyazott <i>-t is tartalmazó) lábjegyzeteket."""
    s = SUP_MARKER.sub("", s)
    out, i = [], 0
    while True:
        j = s.find(FOOTNOTE_OPEN, i)
        if j < 0:
            out.append(s[i:])
            break
        out.append(s[i:j])
        depth, k = 1, j + len(FOOTNOTE_OPEN)
        while depth and k < len(s):
            o = s.find("<i", k)
            c = s.find("</i>", k)
            if c < 0:
                k = len(s)
                break
            if 0 <= o < c:
                depth += 1
                k = o + 2
            else:
                depth -= 1
                k = c + 4
        i = k
    return "".join(out)


def flatten(x) -> str:
    if isinstance(x, str):
        return x
    if isinstance(x, list):
        return "\n".join(p for p in (flatten(e) for e in x) if p.strip())
    return ""


def clean_text(raw: str, keep_nikud: bool = False) -> str:
    s = strip_footnotes(raw)
    s = s.replace("<br>", "\n").replace("<br/>", "\n")
    s = s.replace("<b>", "«").replace("</b>", "»")  # dibur hámátchil jelölése
    s = TAG.sub("", s)
    s = html.unescape(s)
    s = s.replace("\u05BE", "-")  # makkef ne olvadjon össze a szavakkal
    if not keep_nikud:
        s = NIKUD.sub("", s)
    lines = [SPACES.sub(" ", ln).strip() for ln in s.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def pick(entry: dict, lang: str):
    he, en = flatten(entry.get("he")), flatten(entry.get("text"))
    order = [("he", he), ("en", en)] if lang in ("auto", "he") else [("en", en), ("he", he)]
    for code, txt in order:
        if txt.strip():
            return code, txt
    return None, ""


def clean_links(data, lang="auto", keep_nikud=False, anchor=None, category="Commentary"):
    """`data`: a Sefaria /api/links válasza (lista). `anchor`: csak az pontosan erre a versre horgonyzott
    bejegyzések (a több versre kiterjedő kommentárok kimaradnak); None = nincs szűrés."""
    groups = OrderedDict()
    for e in data:
        if category and e.get("category") != category:
            continue
        if anchor and e.get("anchorRef") != anchor:
            continue
        code, raw = pick(e, lang)
        txt = clean_text(raw, keep_nikud)
        if not txt:
            continue
        name = (e.get("collectiveTitle") or {}).get("en") or e.get("index_title") or "?"
        g = groups.setdefault(name, {"name": name, "entries": []})
        g["entries"].append((e.get("commentaryNum", 0), e.get("sourceRef", ""), code, txt))
    result = []
    for name, g in groups.items():
        g["entries"].sort(key=lambda t: t[0])
        langs = sorted({t[2] for t in g["entries"]})
        body = "\n".join(f"[{ref}]\n{txt}" for _, ref, _, txt in g["entries"])
        result.append({"name": name, "lang": "+".join(langs), "text": body, "chars": len(body),
                       "entries": [{"ref": ref, "text": txt} for _, ref, _, txt in g["entries"]]})
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("links_json")
    ap.add_argument("--lang", default="auto", choices=["auto", "he", "en"])
    ap.add_argument("--keep-nikud", action="store_true")
    ap.add_argument("--out", default="cleaned.json")
    ap.add_argument("--anchor", help='csak az erre a versre horgonyzott bejegyzések, pl. "Genesis 17:17"')
    a = ap.parse_args()
    with open(a.links_json, encoding="utf-8") as f:
        res = clean_links(json.load(f), a.lang, a.keep_nikud, a.anchor)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    total = sum(r["chars"] for r in res)
    print(f"{len(res)} magyarázó, {total} karakter (~{total // 3} token durván) -> {a.out}")
