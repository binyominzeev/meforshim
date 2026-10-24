#!/usr/bin/env python3
"""Pászuk-kommentár kérdés-térkép (v2).

tisztítás -> párhuzamos kinyerés (magyarázónként) -> ellenőrzés/szűrés -> párhuzamos összevonás (horgonyonként) -> nested lista

Használat:
    ANTHROPIC_API_KEY a .env fájlban (lásd .env.example) vagy környezeti változóként
    python pipeline.py links.json --dry-run      # csak tisztít, nem hív API-t
    python pipeline.py links.json                # teljes futás
"""
import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from collections import OrderedDict
from difflib import SequenceMatcher
from pathlib import Path

from clean import NIKUD, clean_links
from env import load_env

load_env()  # .env a script mappájából; a benchmark.py is ezen keresztül kapja

API_KEY = ""  # üresen hagyva az ANTHROPIC_API_KEY környezeti változót használja

# Árak USD / millió token (2026. októberi állapot, ellenőrizd: platform.claude.com/docs/en/about-claude/pricing)
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),   # 100K tokenes prompt fölött drágább sáv
    "claude-sonnet-5-5": (2.00, 10.00),
}

VERSE = "וַיִּפֹּ֧ל אַבְרָהָ֛ם עַל־פָּנָ֖יו וַיִּצְחָ֑ק וַיֹּ֣אמֶר בְּלִבּ֗וֹ הַלְּבֶ֤ן מֵאָֽה־שָׁנָה֙ יִוָּלֵ֔ד וְאִ֨ם־שָׂרָ֔ה הֲבַת־תִּשְׁעִ֥ים שָׁנָ֖ה תֵּלֵֽד"
SEGMENTS = OrderedDict([
    ("S1", "וַיִּפֹּל אַבְרָהָם עַל־פָּנָיו"),
    ("S2", "וַיִּצְחָק"),
    ("S3", "וַיֹּאמֶר בְּלִבּוֹ"),
    ("S4", "הַלְּבֶן מֵאָה־שָׁנָה יִוָּלֵד"),
    ("S5", "וְאִם־שָׂרָה"),
    ("S6", "הֲבַת־תִּשְׁעִים שָׁנָה תֵּלֵד"),
])
CTX_TITLE = "Nem konkrét szóhoz kötött, kontextuális kérdések"

# Egységes írásmód. A prompt kéri, az utófeldolgozás kikényszeríti (NAME_FIXES). Itt állítható át.
NAMES_HINT = ("Ávráhám, Sára, Jichák, Jismáél, Hágár, Ketúra, Nóach, Onkelosz, Rashi, "
              "Re'm (= Mizrachi, Rashi szuperkommentátora; Ra'am/Ra\"m/Re'em/Ráma helyett mindig Re'm)")
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

EXTRACT_SYSTEM = f"""You analyze one classical Jewish commentary on a Torah verse, to build a map of the QUESTIONS the commentators address.

VERSE (Genesis 17:17):
{VERSE}

Verse segments (keys for "anchor"):
""" + "\n".join(f"{k}: {v}" for k, v in SEGMENTS.items()) + f"""
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
- Use S1..S6 when the question arises from that phrase and is answered by what that phrase says or means: its wording, meaning, form, grammar, spelling, vocalization, cantillation, translation, or the content the phrase itself expresses.
- Use CTX when the question needs the whole verse or several phrases to be understood, or goes beyond the verse: Abraham's faith/motives overall, the relation to Sarah's laughter (ch. 18), to Ishmael (v. 18) or other verses, theology, the nature of miracles, chronology, midrashic additions, narrative gaps.
- Examples: "Does vayitzchak mean joy or mockery?" -> S2. "Why was Sarah rebuked and Abraham not?" -> CTX. "Did Abraham doubt God's promise?" -> CTX. "Why is 've'im' repeated before Sarah?" -> S5.

CROSS-REFERENCES: if the commentator (or an entry in it) only points elsewhere ("see Radak", "see 18:13", "see Bereshit Rabbah") without content of its own, do NOT create an item; add a one-line Hungarian note to "pointers".

STYLE:
- "question_hu": Hungarian, self-contained, phrased about the verse itself (not "why does X say..."), so the same question from different commentators gets similar wording. Keep Hebrew words in Hebrew.
- "answer_hu": ONE short Hungarian sentence with this commentator's answer.
- Use exactly these spellings in Hungarian text: {NAMES_HINT}.
- Use only Latin or Hebrew letters; never any other script.

Output ONLY valid JSON, no prose, no code fences:
{{"pointers":["..."],"items":[{{"anchor":"S4","explicit":true,"target":null,"question_hu":"...","answer_hu":"...","quote":"..."}}]}}"""

MERGE_SYSTEM = f"""You receive questions about Genesis 17:17 extracted from different commentators. All lines belong to ONE group: {{group_desc}}.
Line format: id | explicit/implicit | target | question || answer (shortened)

Task: cluster lines that ask the SAME question.
- Same topic is NOT enough. A debate usually holds several distinct sub-questions (for example: whether the age is unusual at all / whether the age-to-lifespan ratio still leaves the difficulty / whether Noah is a valid precedent). Keep such sub-questions as separate clusters.
- If a cluster would exceed about 8 lines, check whether it mixes sub-questions, and split it.
- Singletons are fine. Every id must appear in exactly one cluster.
- "question_hu": a precise, neutral Hungarian formulation, specific enough to tell it apart from neighbouring clusters.
- Use exactly these spellings: {NAMES_HINT}.
Output ONLY valid JSON, no prose, no code fences:
{{"clusters":[{{"question_hu":"...","ids":[1,5,7]}}]}}"""


# ---------- segédek ----------

class Meter:
    def __init__(self):
        self.cost = 0.0
        self.tin = 0
        self.tout = 0

    def add(self, model, usage):
        pin, pout = PRICES.get(model, (0, 0))
        self.tin += usage.input_tokens
        self.tout += usage.output_tokens
        self.cost += usage.input_tokens / 1e6 * pin + usage.output_tokens / 1e6 * pout


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


async def call_model(client, model, system, user, max_tokens, meter):
    """Egy hívás + egyszeri újrapróbálás hibás JSON esetén. (A hálózati hibákat/429-et az SDK próbálja újra.)"""
    for attempt in (1, 2):
        resp = await client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        )
        meter.add(model, resp.usage)
        text = "".join(b.text for b in resp.content if b.type == "text")
        if resp.stop_reason == "max_tokens":
            print(f"  ! max_tokens elérve ({model}), a kimenet csonka lehet", file=sys.stderr)
        try:
            return parse_json(text)
        except json.JSONDecodeError:
            if attempt == 2:
                raise
    raise RuntimeError("unreachable")


# ---------- 2. lépés: kinyerés ----------

async def extract_all(client, commentators, a, meter, outdir):
    sem = asyncio.Semaphore(a.concurrency)
    cache_dir = outdir / "extract"
    cache_dir.mkdir(parents=True, exist_ok=True)

    async def one(c):
        f = cache_dir / f"{slug(c['name'])}.json"
        h = hashlib.sha1((EXTRACT_SYSTEM + a.extract_model + c["text"]).encode("utf-8")).hexdigest()
        if f.exists() and not a.force:
            cached = json.loads(f.read_text(encoding="utf-8"))
            if cached.get("hash") == h:  # prompt/modell/szöveg nem változott
                return c["name"], cached
        async with sem:
            t0 = time.time()
            data = await call_model(client, a.extract_model, EXTRACT_SYSTEM,
                                    f"Commentator: {c['name']}\n\n{c['text']}", a.extract_max_tokens, meter)
            rec = {"hash": h, "items": data.get("items", []), "pointers": data.get("pointers", [])}
            f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  {c['name']}: {len(rec['items'])} kérdés, {len(rec['pointers'])} utalás ({time.time() - t0:.1f}s)")
            return c["name"], rec

    return await asyncio.gather(*(one(c) for c in commentators))


def build_items(extracted, commentators):
    """Tisztítás, idézet-ellenőrzés, ismétlődések kiszűrése. Visszaadja: (items, pointers, statisztika)."""
    texts = {c["name"]: norm(c["text"]) for c in commentators}
    words = {n: set(t.split()) for n, t in texts.items()}
    items, pointers, i = [], [], 0
    for name, rec in extracted:
        for p in rec.get("pointers", []):
            p = normalize_hu(p)
            if p:
                pointers.append({"commentator": name, "note": p})
        for it in rec.get("items", []):
            q, ans = normalize_hu(it.get("question_hu", "")), normalize_hu(it.get("answer_hu", ""))
            if not q or not ans:
                continue
            i += 1
            quote = it.get("quote", "") or ""
            items.append({"id": i, "commentator": name,
                          "anchor": it.get("anchor") if it.get("anchor") in SEGMENTS else "CTX",
                          "explicit": bool(it.get("explicit")), "target": it.get("target"),
                          "question": q, "answer": ans, "quote": quote,
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

async def merge_group(client, key, group_items, a, meter):
    if len(group_items) == 1:
        x = group_items[0]
        return [{"anchor": key, "question_hu": x["question"], "ids": [x["id"]]}]
    desc = ("questions about the wider context/theme, not tied to one specific phrase" if key == "CTX"
            else f"questions tied to the phrase {SEGMENTS[key]}")
    lines = "\n".join(
        f"{x['id']} | {'explicit' if x['explicit'] else 'implicit'} | {x['target'] or '-'} | "
        f"{x['question']} || {x['answer'][:200]}" for x in group_items)
    data = await call_model(client, a.merge_model, MERGE_SYSTEM.replace("{group_desc}", desc),
                            lines, a.merge_max_tokens, meter)
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


async def merge_all(client, items, a, meter):
    groups = OrderedDict((k, []) for k in ["CTX", *SEGMENTS])
    for x in items:
        groups[x["anchor"]].append(x)
    results = await asyncio.gather(*(merge_group(client, k, g, a, meter) for k, g in groups.items() if g))
    return [c for r in results for c in r]


# ---------- megjelenítés ----------

def build_result(clusters, items, pointers):
    by_id = {x["id"]: x for x in items}
    groups = OrderedDict((k, []) for k in ["CTX", *SEGMENTS])
    for c in clusters:
        groups[c["anchor"]].append(c)
    out = {"groups": [], "pointers": pointers}
    for key, cl in groups.items():
        if not cl:
            continue
        cl.sort(key=lambda c: -len({by_id[i]["commentator"] for i in c["ids"]}))
        out["groups"].append({
            "anchor": key, "title": CTX_TITLE if key == "CTX" else SEGMENTS[key], "count": len(cl),
            "clusters": [{
                "question": c["question_hu"],
                "implicit": not any(by_id[i]["explicit"] for i in c["ids"]),
                "n_commentators": len({by_id[i]["commentator"] for i in c["ids"]}),
                "answers": [{"commentator": by_id[i]["commentator"], "answer": by_id[i]["answer"],
                             "quote": by_id[i]["quote"], "verified": by_id[i]["verified"]} for i in c["ids"]],
            } for c in cl]})
    return out


def render_md(result):
    out = ["# Genesis 17:17 — kérdés-térkép", ""]
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

async def main_async(a):
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    commentators = clean_links(a.links_json, a.lang, a.keep_nikud)
    (outdir / "cleaned.json").write_text(json.dumps(commentators, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(c["chars"] for c in commentators)
    print(f"Tisztítás: {len(commentators)} magyarázó, {total} karakter (~{total // 3} token durván)")
    for c in sorted(commentators, key=lambda c: -c["chars"])[:5]:
        print(f"  legnagyobb: {c['name']} [{c['lang']}] {c['chars']} kar.")
    if a.dry_run:
        return

    import anthropic
    client = anthropic.AsyncAnthropic(api_key=API_KEY or None, max_retries=5)
    meter = Meter()

    t1 = time.time()
    print(f"Kinyerés ({a.extract_model}, párhuzamosan: {a.concurrency})")
    extracted = await extract_all(client, commentators, a, meter, outdir)
    items, pointers, st = build_items(extracted, commentators)
    (outdir / "items.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    t2 = time.time()
    print(f"  -> {st['total']} kérdés, {st['dups']} ismétlődés kiszűrve, {len(pointers)} csak-utalás, "
          f"{st['unverified']} nem igazolt idézet ({t2 - t1:.1f}s)")

    print(f"Összevonás ({a.merge_model}, horgonyonként párhuzamosan)")
    clusters = await merge_all(client, items, a, meter)
    (outdir / "clusters.json").write_text(json.dumps(clusters, ensure_ascii=False, indent=1), encoding="utf-8")
    t3 = time.time()
    print(f"  -> {len(clusters)} kérdés-klaszter ({t3 - t2:.1f}s)")

    result = build_result(clusters, items, pointers)
    (outdir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (outdir / "result.md").write_text(render_md(result), encoding="utf-8")
    print(f"\nKész: {outdir / 'result.md'} (+ result.json)")
    print(f"Idő: kinyerés {t2 - t1:.1f}s + összevonás {t3 - t2:.1f}s = {t3 - t0:.1f}s összesen")
    print(f"Token: {meter.tin} in / {meter.tout} out, költség kb. ${meter.cost:.3f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("links_json")
    ap.add_argument("--out", default="out")
    ap.add_argument("--lang", default="auto", choices=["auto", "he", "en"])
    ap.add_argument("--keep-nikud", action="store_true")
    ap.add_argument("--extract-model", default="claude-haiku-5-5")
    ap.add_argument("--merge-model", default="claude-sonnet-5-5")
    ap.add_argument("--concurrency", type=int, default=40)
    ap.add_argument("--extract-max-tokens", type=int, default=8000)
    ap.add_argument("--merge-max-tokens", type=int, default=8000)
    ap.add_argument("--force", action="store_true", help="a gyorsítótárazott kinyerést is újrafuttatja")
    ap.add_argument("--dry-run", action="store_true", help="csak tisztítás, nincs API-hívás")
    asyncio.run(main_async(ap.parse_args()))