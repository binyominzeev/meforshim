#!/usr/bin/env python3
"""Pászuk-kommentár kérdés-térkép (v4): a Tóra bármelyik verséhez.

letöltés (Sefaria) -> tisztítás -> párhuzamos kinyerés (magyarázónként) -> ellenőrzés/szűrés
-> párhuzamos összevonás (horgonyonként) -> nested lista

Használat:
    A kulcsok a .env fájlból (lásd .env.example) vagy környezeti változóból jönnek.
    python pipeline.py "Genesis 17:17" --dry-run          # letöltés + tisztítás, nem hív modellt
    python pipeline.py "Exodus 3:14"                      # alap: gpt-6-luna + gpt-6-luna
    python pipeline.py "Deuteronomy 6:4" --extract-model haiku --merge-model sonnet --tag anthropic-haiku-sonnet

A kimenet: out/<Könyv.fej.vers>/result-<tag>.md és .json (a költséggel együtt), a köztes fájlok ugyanott a <tag>/ mappában.
A letöltött vers és kommentárok (verse.json, links.json) ugyanebbe a mappába gyorsítótárazódnak.
A modellek nevei és árai az llm.py-ban vannak (python benchmark.py "Genesis 17:17" --list).
"""
import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from collections import OrderedDict
from datetime import datetime
from difflib import SequenceMatcher

import sefaria
from clean import NIKUD, clean_links
from llm import complete, cost, resolve

CTX_TITLE = "Nem konkrét szóhoz kötött, kontextuális kérdések"

# Egységes írásmód. A prompt kéri, az utófeldolgozás kikényszeríti (NAME_FIXES). Itt állítható át.
NAMES_HINT = ("Ávráhám, Sára, Jichák, Jismáél, Hágár, Ketúra, Nóach, Onkelosz, Rashi, "
              "Re'm (= Mizrachi, Rashi szuperkommentátora; Ra'am/Ra\"m/Re'em/Ráma helyett mindig Re'm). "
              "Other names: the same Hebrew-based Hungarian transliteration style (e.g. Mose, Áron, Jaákov), consistently")
NAME_FIXES = [
    (r"\b(?:Ábrahám|Abrahám|Avraham|Abraham|Ávrahám)", "Ávráhám"),
    (r"\b(?:Izsák|Icchák|Jicchák|Isaac|Yitzchak)", "Jichák"),
    (r"\b(?:Iszmáel|Iszmáél|Ismáel|Ismáél|Ismael|Jišmáél|Jismáel|Jismael|Ishmael)", "Jismáél"),
    (r"\bHagar", "Hágár"),
    (r"\b(?:Noach|Noah|Nóah)", "Nóach"),
    (r"\bRási", "Rashi"),
    (r"\bOnkelos(?!z)", "Onkelosz"),
    (r"\b(?:Ra'am|Ra\"m|Re'em|Ráma|Ramah|Ra'm|Rá\"m|Ra\"am)\b", "Re'm"),
]

def extract_system(v):
    segs = "\n".join(f"{k}: {t}" for k, t in v.segments.items())
    last = list(v.segments)[-1]
    return f"""You analyze one classical Jewish commentary on a Torah verse, to build a map of the QUESTIONS the commentators address.

VERSE ({v.ref}):
{v.text}

Verse segments (keys for "anchor"):
{segs}
CTX: not tied to a specific phrase (see rules).

You receive the text of ONE commentator on this verse. List every question/difficulty that this commentator addresses.

FIDELITY (the most important rule):
- Report only what THIS commentator's text says. Never add interpretation, labels (e.g. "doubting", "rhetorical", "miracle") or background knowledge unless the commentator himself says so.
- If he quotes another view and rejects it, report the position HE takes.
- "quote": copy VERBATIM 3-10 consecutive words from the commentator's text, in the language you were given (no translation, no ellipsis), that support the answer.
- If you are unsure what he means, omit the item instead of guessing.

QUESTIONS:
- explicit=true: he states it as a question/difficulty. explicit=false: he does not state it, but his comment clearly resolves it (infer the underlying question).
- Include linguistic/text-critical questions (word meaning, grammar, vocalization, cantillation, translation).
- If the question is raised about ANOTHER commentator (e.g. a supercommentary on Rashi), set "target" to that name; otherwise null.
- Ignore content about other verses unless it is needed for the answer.

ANCHOR RULES:
- Use S1..{last} when the question arises from that phrase and is answered by what that phrase says or means: its wording, meaning, form, grammar, spelling, vocalization, cantillation, translation, or the content the phrase itself expresses.
- Use CTX when the question needs the whole verse or several phrases to be understood, or goes beyond the verse: the characters' motives overall, the relation to earlier or later verses and chapters, theology, the nature of miracles, chronology, midrashic additions, narrative gaps.
- Illustration (generic): "Does word X mean A or B?" -> the segment containing X. "Why is person P rebuked and person Q not?" -> CTX. "Did P doubt the promise?" -> CTX. "Why is word Y repeated before Z?" -> the segment containing Y or Z.

CROSS-REFERENCES: if the commentator (or an entry in it) only points elsewhere ("see Radak", "see another verse", "see Bereshit Rabbah") without content of its own, do NOT create an item; add a one-line Hungarian note to "pointers".

STYLE:
- "question_hu": Hungarian, self-contained, phrased about the verse itself (not "why does X say..."), so the same question from different commentators gets similar wording. Keep Hebrew words in Hebrew.
- "answer_hu": ONE short Hungarian sentence with this commentator's answer.
- Use exactly these spellings in Hungarian text: {NAMES_HINT}.
- Use only Latin or Hebrew letters; never any other script.

Output ONLY valid JSON, no prose, no code fences:
{{"pointers":["..."],"items":[{{"anchor":"{last}","explicit":true,"target":null,"question_hu":"...","answer_hu":"...","quote":"..."}}]}}"""

def merge_system(v):
    """A "{group_desc}" helyére a hívó írja a csoport leírását."""
    return f"""You receive questions about {v.ref} extracted from different commentators. All lines belong to ONE group: {{group_desc}}.
Line format: id | explicit/implicit | target | question || answer (shortened)

Task: cluster lines that ask the SAME question.
- Same topic is NOT enough. A debate usually holds several distinct sub-questions (for example: whether something is unusual at all / whether a proposed resolution really removes the difficulty / whether a cited precedent is valid). Keep such sub-questions as separate clusters.
- If a cluster would exceed about 8 lines, check whether it mixes sub-questions, and split it.
- Singletons are fine. Every id must appear in exactly one cluster.
- "question_hu": a precise, neutral Hungarian formulation, specific enough to tell it apart from neighbouring clusters.
- Use exactly these spellings: {NAMES_HINT}.
Output ONLY valid JSON, no prose, no code fences:
{{"clusters":[{{"question_hu":"...","ids":[1,5,7]}}]}}"""


# ---------- segédek ----------

class Meter:
    """Token- és költségnyilvántartás lépésenként (kinyerés / összevonás). A gyorsítótárból jövő hívásokat is
    beleszámolja (a futás "teljes költsége"), de külön jelzi, ha egy gyorsítótár-rekordban nincs token (ismeretlen)."""

    def __init__(self):
        self.steps = {}

    def _step(self, step, spec):
        return self.steps.setdefault(step, {"model": spec["name"], "tin": 0, "tout": 0, "calls": 0,
                                            "cached_calls": 0, "cost": 0.0, "unknown": False})

    def add(self, step, spec, tin, tout, cached=False):
        s = self._step(step, spec)
        s["tin"] += tin
        s["tout"] += tout
        s["cached_calls" if cached else "calls"] += 1
        c = cost(spec, tin, tout)
        if c is None:
            s["unknown"] = True
        else:
            s["cost"] += c

    def add_unknown_cached(self, step, spec):
        s = self._step(step, spec)
        s["cached_calls"] += 1
        s["unknown"] = True

    @property
    def total(self):
        return sum(s["cost"] for s in self.steps.values())

    @property
    def incomplete(self):
        return any(s["unknown"] for s in self.steps.values())

    def summary(self):
        return {k: dict(v, cost=round(v["cost"], 6)) for k, v in self.steps.items()}


def parse_json(text: str):
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        s, e = t.find("{"), t.rfind("}")
        return json.loads(t[s:e + 1])


def _allowed(ch: str) -> bool:
    o = ord(ch)
    return (o < 0x250 or 0x1E00 <= o <= 0x1EFF or 0x0590 <= o <= 0x05FF
            or 0x2000 <= o <= 0x206F or 0x2190 <= o <= 0x21FF)


def sanitize(s: str) -> str:
    """Kidobja a nem latin/héber írásjegyeket (pl. a Haiku-tól néha becsúszó grúz betűt)."""
    s = re.sub("[\u200b-\u200f\u202a-\u202e]", "", s or "")
    return "".join(ch for ch in s if _allowed(ch)).strip()


def normalize_hu(s: str) -> str:
    s = sanitize(s)
    for pat, rep in NAME_FIXES:
        s = re.sub(pat, rep, s)
    return s


def norm(s: str) -> str:
    s = NIKUD.sub("", (s or "").lower())
    return " ".join(re.sub(r"[^\w\s]", " ", s).split())


def quote_ok(quote: str, text_norm: str, words: set) -> bool:
    """Puha ellenőrzés: az idézet szó szerint (vagy legalább 80%-ban) szerepel-e a magyarázó szövegében."""
    q = norm(quote)
    if not q:
        return False
    if q in text_norm:
        return True
    qw = q.split()
    return len(qw) >= 3 and sum(w in words for w in qw) / len(qw) >= 0.8


def slug(name):
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


async def call_model(spec, system, user, max_tokens, meter, step):
    """Egy hívás + egyszeri újrapróbálás hibás JSON esetén. (A hálózati hibákat/429-et az SDK próbálja újra.)
    Visszaad: (adat, bemeneti token, kimeneti token, másodperc) az esetleges újrapróbálással együtt."""
    tin = tout = 0
    t0 = time.time()
    for attempt in (1, 2):
        r = await complete(spec, system, user, max_tokens)
        if r.error:  # pl. elfogyott kredit: inkább álljon meg, mint hogy üres eredményt gyorsítótárazzon
            raise RuntimeError(f"{spec['name']}: {r.error}")
        meter.add(step, spec, r.tin, r.tout)
        tin, tout = tin + r.tin, tout + r.tout
        if r.truncated:
            print(f"  ! max_tokens elérve ({spec['name']}), a kimenet csonka lehet", file=sys.stderr)
        try:
            return parse_json(r.text), tin, tout, time.time() - t0
        except json.JSONDecodeError:
            if attempt == 2:
                raise
    raise RuntimeError("unreachable")


# ---------- 2. lépés: kinyerés ----------

def spec_key(spec):
    """A gyorsítótár-kulcs része: a modell és a nem alapértelmezett paraméterei."""
    return spec["model"] + (json.dumps(spec["extra"], sort_keys=True) if spec.get("extra") else "")


async def extract_all(spec, system, commentators, a, meter, workdir):
    sem = asyncio.Semaphore(a.concurrency)
    cache_dir = workdir / "extract"
    cache_dir.mkdir(parents=True, exist_ok=True)

    async def one(c):
        f = cache_dir / f"{slug(c['name'])}.json"
        h = hashlib.sha1((system + spec_key(spec) + c["text"]).encode("utf-8")).hexdigest()
        if f.exists() and not a.force:
            cached = json.loads(f.read_text(encoding="utf-8"))
            if cached.get("hash") == h:  # prompt/modell/szöveg nem változott
                if "tin" in cached:
                    meter.add("extract", spec, cached["tin"], cached["tout"], cached=True)
                else:
                    meter.add_unknown_cached("extract", spec)
                return c["name"], cached
        async with sem:
            data, tin, tout, secs = await call_model(spec, system, f"Commentator: {c['name']}\n\n{c['text']}",
                                                     a.extract_max_tokens, meter, "extract")
            rec = {"hash": h, "items": data.get("items", []), "pointers": data.get("pointers", []),
                   "tin": tin, "tout": tout}
            f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  {c['name']}: {len(rec['items'])} kérdés, {len(rec['pointers'])} utalás ({secs:.1f}s)")
            return c["name"], rec

    return await asyncio.gather(*(one(c) for c in commentators))


def source_ref(entries, quote):
    """A magyarázó azon bejegyzésének Sefaria-hivatkozása, amelyből az idézet származik."""
    if not entries:
        return None
    q = norm(quote)
    qw = set(q.split())
    best, score = entries[0]["ref"], -1
    for e in entries:
        t = norm(e["text"])
        if q and q in t:
            return e["ref"]
        s = len(qw & set(t.split()))
        if s > score:
            best, score = e["ref"], s
    return best


def build_items(extracted, commentators, anchors):
    """Tisztítás, idézet-ellenőrzés, ismétlődések kiszűrése. `anchors`: az érvényes szegmenskulcsok.
    Visszaadja: (items, pointers, statisztika)."""
    texts = {c["name"]: norm(c["text"]) for c in commentators}
    words = {n: set(t.split()) for n, t in texts.items()}
    entries = {c["name"]: c.get("entries", []) for c in commentators}
    items, pointers, i = [], [], 0
    for name, rec in extracted:
        for p in rec.get("pointers", []):
            p = normalize_hu(p)
            if p:
                pointers.append({"commentator": name, "note": p,
                                 "ref": entries[name][0]["ref"] if entries[name] else None})
        for it in rec.get("items", []):
            q, ans = normalize_hu(it.get("question_hu", "")), normalize_hu(it.get("answer_hu", ""))
            if not q or not ans:
                continue
            i += 1
            quote = it.get("quote", "") or ""
            items.append({"id": i, "commentator": name,
                          "anchor": it.get("anchor") if it.get("anchor") in anchors else "CTX",
                          "explicit": bool(it.get("explicit")), "target": it.get("target"),
                          "question": q, "answer": ans, "quote": quote,
                          "ref": source_ref(entries[name], quote),
                          "verified": quote_ok(quote, texts[name], words[name])})
    kept, dups = [], 0
    for x in items:  # ugyanattól a magyarázótól közel azonos válasz = ismétlődés
        if any(k["commentator"] == x["commentator"]
               and SequenceMatcher(None, k["answer"], x["answer"]).ratio() > 0.8 for k in kept):
            dups += 1
            continue
        kept.append(x)
    stats = {"total": len(items), "dups": dups, "unverified": sum(not x["verified"] for x in kept)}
    return kept, pointers, stats


# ---------- 3. lépés: összevonás horgonyonként, párhuzamosan ----------

async def merge_group(spec, v, key, group_items, a, meter):
    if len(group_items) == 1:
        x = group_items[0]
        return [{"anchor": key, "question_hu": x["question"], "ids": [x["id"]]}]
    desc = ("questions about the wider context/theme, not tied to one specific phrase" if key == "CTX"
            else f"questions tied to the phrase {v.segments[key]}")
    lines = "\n".join(
        f"{x['id']} | {'explicit' if x['explicit'] else 'implicit'} | {x['target'] or '-'} | "
        f"{x['question']} || {x['answer'][:200]}" for x in group_items)
    data, *_ = await call_model(spec, merge_system(v).replace("{group_desc}", desc), lines,
                                a.merge_max_tokens, meter, "merge")
    valid = {x["id"] for x in group_items}
    by_id = {x["id"]: x for x in group_items}
    seen, clusters = set(), []
    for c in data.get("clusters", []):
        ids = [i for i in c.get("ids", []) if i in valid and i not in seen]
        seen.update(ids)
        if ids:
            clusters.append({"anchor": key, "question_hu": normalize_hu(c.get("question_hu", "")), "ids": ids})
    for i in sorted(valid - seen):  # amit a modell kihagyott, az önálló klaszter lesz
        clusters.append({"anchor": key, "question_hu": by_id[i]["question"], "ids": [i]})
    return clusters


async def merge_all(spec, v, items, a, meter):
    groups = OrderedDict((k, []) for k in ["CTX", *v.segments])
    for x in items:
        groups[x["anchor"]].append(x)
    results = await asyncio.gather(*(merge_group(spec, v, k, g, a, meter) for k, g in groups.items() if g))
    return [c for r in results for c in r]


# ---------- megjelenítés ----------

def build_result(v, clusters, items, pointers):
    by_id = {x["id"]: x for x in items}
    groups = OrderedDict((k, []) for k in ["CTX", *v.segments])
    for c in clusters:
        groups[c["anchor"]].append(c)
    out = {"groups": [], "pointers": pointers}
    for key, cl in groups.items():
        if not cl:
            continue
        cl.sort(key=lambda c: -len({by_id[i]["commentator"] for i in c["ids"]}))
        out["groups"].append({
            "anchor": key, "title": CTX_TITLE if key == "CTX" else v.segments[key], "count": len(cl),
            "clusters": [{
                "question": c["question_hu"],
                "implicit": not any(by_id[i]["explicit"] for i in c["ids"]),
                "n_commentators": len({by_id[i]["commentator"] for i in c["ids"]}),
                "answers": [{"commentator": by_id[i]["commentator"], "answer": by_id[i]["answer"],
                             "quote": by_id[i]["quote"], "ref": by_id[i]["ref"],
                             "verified": by_id[i]["verified"]} for i in c["ids"]],
            } for c in cl]})
    return out


def render_md(v, result):
    out = [f"# {v.ref} — kérdés-térkép", ""]
    for g in result["groups"]:
        out.append(f"- **{g['title']} ({g['count']})**")
        for c in g["clusters"]:
            out.append(f"    - {c['question']}{' [implicit]' if c['implicit'] else ''} ({c['n_commentators']})")
            for ans in c["answers"]:
                mark = "" if ans["verified"] else " ⚠"
                out.append(f"        - {ans['answer']} ({ans['commentator']}{mark})")
    if result["pointers"]:
        out += ["", "## Csak utalást tartalmazó bejegyzések (nem számítanak kérdésnek)"]
        out += [f"- {p['commentator']}: {p['note']}" for p in result["pointers"]]
    out += ["", "⚠ = az alátámasztó idézet nem található a magyarázó szövegében, kézzel ellenőrizendő."]
    return "\n".join(out) + "\n"


# ---------- futtatás ----------

def build_meta(v, tag, ex, mg, meter, secs, st):
    def step_cost(k):
        s = meter.steps.get(k)
        return None if not s or s["unknown"] else round(s["cost"], 6)

    costs = {k: step_cost(k) for k in ("extract", "merge")}
    total = None if any(v is None for v in costs.values()) else round(sum(costs.values()), 6)
    return {
        "ref": v.ref, "tag": tag, "extract_model": ex["name"], "merge_model": mg["name"],
        "providers": sorted({ex["provider"], mg["provider"]}),
        "cost": {**costs, "total": total}, "estimated": False,
        "tokens": {"in": sum(s["tin"] for s in meter.steps.values()), "out": sum(s["tout"] for s in meter.steps.values())},
        "calls": sum(s["calls"] for s in meter.steps.values()),
        "cached_calls": sum(s["cached_calls"] for s in meter.steps.values()),
        "seconds": secs, "generated": datetime.now().isoformat(timespec="seconds"),
        "stats": {"questions": st["total"] - st["dups"], "unverified": st["unverified"]},
    }


async def main_async(a):
    ex_spec, mg_spec = resolve(a.extract_model), resolve(a.merge_model)
    tag = a.tag or (ex_spec["name"] if ex_spec is mg_spec else f"{ex_spec['name']}-{mg_spec['name']}")
    t0 = time.time()
    verse, links, outdir = sefaria.prepare(a)
    print(f"Vers: {verse.ref}  {verse.text}")
    print("Szegmensek: " + " | ".join(f"{k}={t}" for k, t in verse.segments.items()))
    workdir = outdir / tag
    workdir.mkdir(parents=True, exist_ok=True)
    commentators = clean_links(links, a.lang, a.keep_nikud, anchor=verse.ref)
    if not commentators:
        sys.exit(f"Nincs kommentár a(z) {verse.ref} versre a Sefarián.")
    (workdir / "cleaned.json").write_text(json.dumps(commentators, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(c["chars"] for c in commentators)
    print(f"Tisztítás: {len(commentators)} magyarázó, {total} karakter (~{total // 3} token durván)")
    for c in sorted(commentators, key=lambda c: -c["chars"])[:5]:
        print(f"  legnagyobb: {c['name']} [{c['lang']}] {c['chars']} kar.")
    if a.dry_run:
        return

    meter = Meter()

    t1 = time.time()
    print(f"Kinyerés ({ex_spec['name']}, párhuzamosan: {a.concurrency})")
    extracted = await extract_all(ex_spec, extract_system(verse), commentators, a, meter, workdir)
    items, pointers, st = build_items(extracted, commentators, verse.segments)
    (workdir / "items.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    t2 = time.time()
    print(f"  -> {st['total']} kérdés, {st['dups']} ismétlődés kiszűrve, {len(pointers)} csak-utalás, "
          f"{st['unverified']} nem igazolt idézet ({t2 - t1:.1f}s)")

    print(f"Összevonás ({mg_spec['name']}, horgonyonként párhuzamosan)")
    clusters = await merge_all(mg_spec, verse, items, a, meter)
    (workdir / "clusters.json").write_text(json.dumps(clusters, ensure_ascii=False, indent=1), encoding="utf-8")
    t3 = time.time()
    print(f"  -> {len(clusters)} kérdés-klaszter ({t3 - t2:.1f}s)")

    result = build_result(verse, clusters, items, pointers)
    result["meta"] = build_meta(verse, tag, ex_spec, mg_spec, meter,
                                {"extract": round(t2 - t1, 1), "merge": round(t3 - t2, 1), "total": round(t3 - t0, 1)}, st)
    (outdir / f"result-{tag}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (outdir / f"result-{tag}.md").write_text(render_md(verse, result), encoding="utf-8")
    print(f"\nKész: {outdir / f'result-{tag}.md'} (+ .json)")
    print(f"Idő: kinyerés {t2 - t1:.1f}s + összevonás {t3 - t2:.1f}s = {t3 - t0:.1f}s összesen")
    m = result["meta"]
    print(f"Token: {m['tokens']['in']} in / {m['tokens']['out']} out")
    for k, s in meter.summary().items():
        print(f"Költség ({k}, {s['model']}): " + ("ismeretlen ár/token" if s["unknown"] else f"${s['cost']:.4f}")
              + f"  [{s['calls']} új + {s['cached_calls']} gyorsítótárazott hívás]")
    print("Költség összesen: " + ("hiányos" if m["cost"]["total"] is None else f"${m['cost']['total']:.4f}"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sefaria.add_args(ap)
    ap.add_argument("--tag", help="a futás neve a kimeneti fájlokban (alap: a modellnevek)")
    ap.add_argument("--lang", default="auto", choices=["auto", "he", "en"])
    ap.add_argument("--keep-nikud", action="store_true")
    ap.add_argument("--extract-model", default="gpt-6-luna", help="modellnév vagy -azonosító (lásd llm.py)")
    ap.add_argument("--merge-model", default="gpt-6-luna", help="modellnév vagy -azonosító (lásd llm.py)")
    ap.add_argument("--concurrency", type=int, default=40)
    ap.add_argument("--extract-max-tokens", type=int, default=8000)
    ap.add_argument("--merge-max-tokens", type=int, default=8000)
    ap.add_argument("--force", action="store_true", help="a gyorsítótárazott kinyerést is újrafuttatja")
    ap.add_argument("--dry-run", action="store_true", help="csak tisztítás, nincs API-hívás")
    asyncio.run(main_async(ap.parse_args()))