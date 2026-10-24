#!/usr/bin/env python3
"""Pászuk-kommentár kérdés-térkép: tisztítás -> párhuzamos kinyerés -> összevonás -> nested lista.

Használat:
    export ANTHROPIC_API_KEY=...        (vagy írd be az API_KEY konstansba)
    python pipeline.py links.json --dry-run      # csak tisztít, nem hív API-t
    python pipeline.py links.json                # teljes futás
"""
import argparse
import asyncio
import json
import re
import sys
import time
from collections import OrderedDict
from pathlib import Path

from clean import clean_links

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

EXTRACT_SYSTEM = f"""You analyze classical Jewish Torah commentaries on one verse.

VERSE (Genesis 17:17):
{VERSE}

Verse segments (use these keys as "anchor"):
""" + "\n".join(f"{k}: {v}" for k, v in SEGMENTS.items()) + """
CTX: the question is not about a specific word/phrase but about the wider context or theme
     (e.g. Abraham's character and faith, relation to Sarah's laughter in ch. 18, to Ishmael in v.18, the meaning of the miracle).

You receive the text of ONE commentator on this verse. List every question/difficulty that the commentator addresses:
- explicit=true: he states it as a question or a difficulty.
- explicit=false: he does not state it, but his comment clearly answers it (infer the underlying question).
Include linguistic/text-critical questions (meaning of a word, grammar, vocalization, cantillation) and
questions the commentator raises about ANOTHER commentator (set "target" to that name, e.g. "Rashi"; otherwise null).
Ignore content that is about other verses.

Rules:
- Write "question_hu" in Hungarian, self-contained, phrased about the verse itself (not "why does X say..."),
  so the same question from different commentators gets similar wording. Keep Hebrew words in Hebrew.
- "answer_hu": ONE short Hungarian sentence with this commentator's answer.
- Do not invent questions that the text does not support. If nothing relevant, return an empty list.
- Output ONLY valid JSON, no prose, no code fences:
{"items":[{"anchor":"S4","explicit":true,"target":null,"question_hu":"...","answer_hu":"..."}]}"""

MERGE_SYSTEM = """You receive a numbered list of questions about Genesis 17:17, extracted from different commentators.
Format of each line: id | anchor | explicit | target | question (Hungarian)

Task: group lines that ask essentially the same question into clusters.
- Do not merge genuinely different questions. Singletons are fine.
- Every id must appear in exactly one cluster.
- "question_hu": a clear canonical Hungarian formulation of the cluster's question.
- "anchor": the best-fitting anchor key (S1..S6 or CTX) for the cluster.
Output ONLY valid JSON, no prose, no code fences:
{"clusters":[{"anchor":"S4","question_hu":"...","ids":[1,5,7]}]}"""


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


async def call_model(client, model, system, user, max_tokens, meter):
    """Egy hívás + egyszeri újrapróbálás, ha a JSON hibás. (A hálózati hibákat az SDK maga próbálja újra.)"""
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


def slug(name):
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


async def extract_all(client, commentators, a, meter, outdir):
    sem = asyncio.Semaphore(a.concurrency)
    cache_dir = outdir / "extract"
    cache_dir.mkdir(parents=True, exist_ok=True)

    async def one(c):
        f = cache_dir / f"{slug(c['name'])}.json"
        if f.exists() and not a.force:
            return c["name"], json.loads(f.read_text(encoding="utf-8"))["items"]
        async with sem:
            t0 = time.time()
            data = await call_model(client, a.extract_model, EXTRACT_SYSTEM,
                                    f"Commentator: {c['name']}\n\n{c['text']}", a.extract_max_tokens, meter)
            items = data.get("items", [])
            f.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  {c['name']}: {len(items)} kérdés ({time.time() - t0:.1f}s)")
            return c["name"], items

    return await asyncio.gather(*(one(c) for c in commentators))


def build_items(extracted):
    items, i = [], 0
    for name, its in extracted:
        for it in its:
            i += 1
            anchor = it.get("anchor") if it.get("anchor") in SEGMENTS else "CTX"
            items.append({"id": i, "commentator": name, "anchor": anchor,
                          "explicit": bool(it.get("explicit")), "target": it.get("target"),
                          "question": it.get("question_hu", "").strip(),
                          "answer": it.get("answer_hu", "").strip()})
    return items


async def merge(client, items, a, meter):
    lines = "\n".join(f"{x['id']} | {x['anchor']} | {'explicit' if x['explicit'] else 'implicit'} | "
                      f"{x['target'] or '-'} | {x['question']}" for x in items)
    data = await call_model(client, a.merge_model, MERGE_SYSTEM, lines, a.merge_max_tokens, meter)
    clusters, seen = data.get("clusters", []), set()
    valid = {x["id"] for x in items}
    for c in clusters:
        c["ids"] = [i for i in c.get("ids", []) if i in valid and i not in seen]
        seen.update(c["ids"])
    by_id = {x["id"]: x for x in items}
    for i in sorted(valid - seen):  # amit a modell kihagyott, az önálló klaszter lesz
        clusters.append({"anchor": by_id[i]["anchor"], "question_hu": by_id[i]["question"], "ids": [i]})
    return [c for c in clusters if c["ids"]]


def render(clusters, items):
    by_id = {x["id"]: x for x in items}
    groups = OrderedDict((k, []) for k in ["CTX", *SEGMENTS])
    for c in clusters:
        groups.setdefault(c.get("anchor") if c.get("anchor") in groups else "CTX", []).append(c)
    out = ["# Genesis 17:17 — kérdés-térkép", ""]
    for key, cl in groups.items():
        if not cl:
            continue
        cl.sort(key=lambda c: -len({by_id[i]["commentator"] for i in c["ids"]}))
        title = CTX_TITLE if key == "CTX" else SEGMENTS[key]
        out.append(f"- **{title} ({len(cl)})**")
        for c in cl:
            members = [by_id[i] for i in c["ids"]]
            n = len({m["commentator"] for m in members})
            tag = " [implicit]" if not any(m["explicit"] for m in members) else ""
            out.append(f"    - {c['question_hu']}{tag} ({n})")
            for m in members:
                out.append(f"        - {m['answer']} ({m['commentator']})")
    return "\n".join(out) + "\n"


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
    client = anthropic.AsyncAnthropic(api_key=API_KEY or None, max_retries=3)
    meter = Meter()

    t1 = time.time()
    print(f"Kinyerés ({a.extract_model}, párhuzamosan: {a.concurrency})")
    extracted = await extract_all(client, commentators, a, meter, outdir)
    items = build_items(extracted)
    (outdir / "items.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    t2 = time.time()
    print(f"  -> {len(items)} kérdés összesen, {t2 - t1:.1f}s")

    print(f"Összevonás ({a.merge_model})")
    clusters = await merge(client, items, a, meter)
    (outdir / "clusters.json").write_text(json.dumps(clusters, ensure_ascii=False, indent=1), encoding="utf-8")
    t3 = time.time()
    print(f"  -> {len(clusters)} kérdés-klaszter, {t3 - t2:.1f}s")

    md = render(clusters, items)
    (outdir / "result.md").write_text(md, encoding="utf-8")
    print(f"\nKész: {outdir / 'result.md'}")
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
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--extract-max-tokens", type=int, default=6000)
    ap.add_argument("--merge-max-tokens", type=int, default=8000)
    ap.add_argument("--force", action="store_true", help="a gyorsítótárazott kinyerést is újrafuttatja")
    ap.add_argument("--dry-run", action="store_true", help="csak tisztítás, nincs API-hívás")
    asyncio.run(main_async(ap.parse_args()))
