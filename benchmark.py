#!/usr/bin/env python3
"""Modell-benchmark a pipeline.py két lépésére (kinyerés + összevonás), UGYANAZZAL a prompttal.

Egy reprezentatív, rétegzett mintán fut (alapból 25 magyarázó), és méri:
  Kinyerés : késleltetés (p50/p95), token, költség (+ extrapoláció a teljes anyagra), JSON-hibaarány,
             kérdésszám, idézet-igazolási arány, ismétlődés, idegen írásjegy-szennyezés,
             referenciához mért idézet-lefedettség (nyelvfüggetlen), opcionális LLM-bíró (vak).
  Összevonás: ugyanazokon a tételeken: érvényesség, kihagyott/duplikált id, klaszterméret,
             páros F1 a referencia-klaszterezéshez képest, késleltetés, költség.

Telepítés:  pip install anthropic openai google-genai
Kulcsok:    ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY (vagy GOOGLE_API_KEY); egyéb szolgáltatóhoz a key_env.

Használat:
    python benchmark.py links.json --list
    python benchmark.py links.json --sample 25 --judge sonnet
    python benchmark.py links.json --only haiku,gemini-flash --sample 10
    python benchmark.py links.json --models my_models.json

FONTOS: a modellazonosítók és árak a DEFAULT_MODELS-ben csak kiindulópontok, ELLENŐRIZD és javítsd őket
(vagy adj --models JSON-t). Ahol az ár None, ott a költség "n/a".
"""
import argparse
import asyncio
import json
import os
import random
import re
import statistics
import sys
import time
from collections import OrderedDict
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

import pipeline as P
import sefaria
from clean import clean_links
from llm import FRESH, MODELS as DEFAULT_MODELS, Result, complete, cost  # noqa: F401 - a modell-nyilvántartás az llm.py-ban van

VERSE = None  # a main_async állítja be (sefaria.Verse)


# ---------- minta ----------

def stratified_sample(commentators, n, seed):
    """Hossz szerint rétegzett minta, hogy rövid és hosszú szövegek is benne legyenek."""
    if n >= len(commentators):
        return list(commentators)
    cs = sorted(commentators, key=lambda c: c["chars"])
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        lo, hi = i * len(cs) // n, max((i + 1) * len(cs) // n, i * len(cs) // n + 1)
        out.append(cs[rnd.randrange(lo, hi)])
    return out


# ---------- gyorsítótár ----------

def cached(path: Path, force):
    if path.exists() and not force:
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def store(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    return obj


def prompt_hash(*parts):
    import hashlib
    return hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()[:12]


# ---------- kinyerés ----------

async def run_extract(spec, sample, a, root):
    sem = asyncio.Semaphore(a.concurrency)
    d = root / spec["name"] / "extract"
    ph = prompt_hash(P.extract_system(VERSE), spec["model"], json.dumps(spec.get("extra", {}), sort_keys=True))

    async def one(c):
        f = d / f"{P.slug(c['name'])}.json"
        rec = cached(f, a.force)
        if rec and rec.get("ph") == ph and rec.get("text_hash") == prompt_hash(c["text"]):
            return c, rec
        async with sem:
            r = await complete(spec, P.extract_system(VERSE), f"Commentator: {c['name']}\n\n{c['text']}",
                               a.extract_max_tokens)
        rec = {"ph": ph, "text_hash": prompt_hash(c["text"]), "raw": r.text, "tin": r.tin, "tout": r.tout,
               "truncated": r.truncated, "secs": r.secs, "error": r.error}
        if not r.error:
            store(f, rec)
        return c, rec

    done = await asyncio.gather(*(one(c) for c in sample))
    print(f"  [{spec['name']}] kinyerés kész ({len(done)} hívás)", flush=True)
    return done


def analyse_extract(spec, done, sample):
    lat, tin, tout, fails, trunc, errors = [], 0, 0, 0, 0, 0
    extracted, lost_chars, total_chars, name_fixed = [], 0, 0, 0
    for c, rec in done:
        lat.append(rec["secs"])
        tin += rec["tin"]
        tout += rec["tout"]
        trunc += bool(rec["truncated"])
        if rec["error"]:
            errors += 1
            extracted.append((c["name"], {"items": [], "pointers": []}))
            continue
        try:
            data = P.parse_json(rec["raw"])
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, json.JSONDecodeError):
            fails += 1
            extracted.append((c["name"], {"items": [], "pointers": []}))
            continue
        items = [it for it in data.get("items", []) if isinstance(it, dict)]
        for it in items:
            for k in ("question_hu", "answer_hu"):
                raw = str(it.get(k, ""))
                san = P.sanitize(raw)
                total_chars += len(raw)
                lost_chars += len(raw) - len(san)
                name_fixed += P.normalize_hu(raw) != san
        extracted.append((c["name"], {"items": items, "pointers": data.get("pointers", []) or []}))
    kept, pointers, st = P.build_items(extracted, sample, VERSE.segments)
    n = len(sample)
    cst = cost(spec, tin, tout)
    return {
        "items": kept, "pointers": pointers, "calls": n, "errors": errors, "json_fail": fails, "truncated": trunc,
        "lat_p50": statistics.median(lat), "lat_p95": sorted(lat)[min(len(lat) - 1, int(len(lat) * 0.95))],
        "tin": tin, "tout": tout, "cost": cst,
        "n_items": len(kept), "items_per_comm": len(kept) / n,
        "dup_rate": st["dups"] / max(1, st["total"]),
        "unverified_rate": st["unverified"] / max(1, len(kept)),
        "ctx_share": sum(x["anchor"] == "CTX" for x in kept) / max(1, len(kept)),
        "explicit_share": sum(x["explicit"] for x in kept) / max(1, len(kept)),
        "foreign_char_rate": lost_chars / max(1, total_chars),
        "name_fix_rate": name_fixed / max(1, len(kept)),
    }


def _qwords(x):
    return set(P.norm(x["quote"]).split())


def quote_match(x, y):
    if x["commentator"] != y["commentator"]:
        return False
    a, b = _qwords(x), _qwords(y)
    return bool(a and b) and len(a & b) / min(len(a), len(b)) >= 0.5


def compare_to_ref(items, ref_items):
    """Nyelvfüggetlen egyezés a referenciával a (forrásnyelvű) idézetek átfedése alapján."""
    ref_v = [x for x in ref_items if x["verified"]]
    cand_v = [x for x in items if x["verified"]]
    matched_ref = [(r, next((c for c in cand_v if quote_match(r, c)), None)) for r in ref_v]
    hit = [(r, c) for r, c in matched_ref if c]
    recall = len(hit) / max(1, len(ref_v))
    prec = sum(any(quote_match(c, r) for r in ref_v) for c in cand_v) / max(1, len(cand_v))
    anchor_agree = sum(r["anchor"] == c["anchor"] for r, c in hit) / max(1, len(hit))
    return {"recall_vs_ref": recall, "precision_vs_ref": prec, "anchor_agree": anchor_agree}


# ---------- LLM-bíró (vak, egy jelölt / hívás) ----------

JUDGE_SYSTEM = """You audit an extraction of questions from ONE classical Jewish commentary on Genesis 17:17.
You get the commentator's source text and a list of extracted items (anchor, explicit flag, Hungarian question, answer, quote).
Rate strictly:
- fidelity (1-5): do the answers say only what the commentator says (no added interpretation or background)?
- coverage (1-5): are all real questions/difficulties addressed by the commentator captured?
- anchor (1-5): are S1..S6 vs CTX anchors assigned correctly per the usual rules (phrase-specific -> S*, whole verse/beyond -> CTX)?
- hungarian (1-5): are the Hungarian questions/answers correct, self-contained, fluent?
- hallucinated: number of items NOT supported by the source text.
Output ONLY JSON: {"fidelity":n,"coverage":n,"anchor":n,"hungarian":n,"hallucinated":n}"""


async def run_judge(judge_spec, spec, sample, items, a, root):
    sem = asyncio.Semaphore(a.concurrency)
    d = root / spec["name"] / f"judge_{judge_spec['name']}"
    by_comm = {}
    for x in items:
        by_comm.setdefault(x["commentator"], []).append(x)

    tok = [0, 0]  # a bíró összes tokenje (gyorsítótárazottal együtt)

    async def one(c):
        f = d / f"{P.slug(c['name'])}.json"
        its = by_comm.get(c["name"], [])
        key = prompt_hash(JUDGE_SYSTEM, json.dumps(its, ensure_ascii=False))
        rec = cached(f, a.force)
        if rec and rec.get("key") == key:
            tok[0] += rec.get("tin", 0)
            tok[1] += rec.get("tout", 0)
            return rec["scores"]
        listing = "\n".join(f"- [{x['anchor']}|{'explicit' if x['explicit'] else 'implicit'}] {x['question']} => "
                            f"{x['answer']} (quote: {x['quote']})" for x in its) or "(no items)"
        async with sem:
            r = await complete(judge_spec, JUDGE_SYSTEM, f"SOURCE ({c['name']}):\n{c['text']}\n\nITEMS:\n{listing}", 1000)
        tok[0] += r.tin
        tok[1] += r.tout
        try:
            s = P.parse_json(r.text)
            scores = {k: float(s[k]) for k in ("fidelity", "coverage", "anchor", "hungarian", "hallucinated")}
        except Exception:  # noqa: BLE001
            return None
        store(f, {"key": key, "scores": scores, "tin": r.tin, "tout": r.tout})
        return scores

    res = [s for s in await asyncio.gather(*(one(c) for c in sample)) if s]
    out = {"tin": tok[0], "tout": tok[1]}
    if res:
        out.update({k: statistics.mean(s[k] for s in res) for k in res[0]})
        out["judged"] = len(res)
    return out


# ---------- összevonás ----------

def merge_lines(group_items):
    return "\n".join(f"{x['id']} | {'explicit' if x['explicit'] else 'implicit'} | {x['target'] or '-'} | "
                     f"{x['question']} || {x['answer'][:200]}" for x in group_items)


def pair_set(clusters):
    s = set()
    for ids in clusters:
        s.update(combinations(sorted(ids), 2))
    return s


def pair_f1(cand, ref):
    a, b = pair_set(cand), pair_set(ref)
    if not a and not b:
        return 1.0
    tp = len(a & b)
    p, r = tp / max(1, len(a)), tp / max(1, len(b))
    return 2 * p * r / (p + r) if p + r else 0.0


async def run_merge(spec, groups, a, root):
    sem = asyncio.Semaphore(a.concurrency)
    d = root / spec["name"] / "merge"
    ph = prompt_hash(P.merge_system(VERSE), spec["model"], json.dumps(spec.get("extra", {}), sort_keys=True))

    async def one(key, gitems):
        f = d / f"{key}.json"
        gh = prompt_hash(merge_lines(gitems))
        rec = cached(f, a.force)
        if rec and rec.get("ph") == ph and rec.get("gh") == gh:
            return key, rec
        desc = ("questions about the wider context/theme, not tied to one specific phrase" if key == "CTX"
                else f"questions tied to the phrase {VERSE.segments[key]}")
        async with sem:
            r = await complete(spec, P.merge_system(VERSE).replace("{group_desc}", desc), merge_lines(gitems),
                               a.merge_max_tokens)
        rec = {"ph": ph, "gh": gh, "raw": r.text, "tin": r.tin, "tout": r.tout, "secs": r.secs,
               "truncated": r.truncated, "error": r.error}
        if not r.error:
            store(f, rec)
        return key, rec

    done = await asyncio.gather(*(one(k, g) for k, g in groups.items()))
    print(f"  [{spec['name']}] összevonás kész ({len(done)} csoport)", flush=True)
    return done


def parse_clusters(rec, valid):
    try:
        data = P.parse_json(rec["raw"])
        raw = [[i for i in c.get("ids", []) if isinstance(i, int)] for c in data.get("clusters", [])]
    except Exception:  # noqa: BLE001
        return None
    seen, out, dup, foreign = set(), [], 0, 0
    for ids in raw:
        keep = []
        for i in ids:
            if i not in valid:
                foreign += 1
            elif i in seen:
                dup += 1
            else:
                seen.add(i)
                keep.append(i)
        if keep:
            out.append(keep)
    missing = len(valid - seen)
    out += [[i] for i in sorted(valid - seen)]  # mint a pipeline-ban: kihagyott = önálló klaszter
    return {"clusters": out, "missing": missing, "dup": dup, "foreign": foreign}


def analyse_merge(spec, done, groups, ref_clusters):
    lat, tin, tout, bad = [], 0, 0, 0
    f1s, sizes, missing, dup, nclust, total = [], [], 0, 0, 0, 0
    for key, rec in done:
        lat.append(rec["secs"])
        tin += rec["tin"]
        tout += rec["tout"]
        valid = {x["id"] for x in groups[key]}
        total += len(valid)
        pc = None if rec["error"] else parse_clusters(rec, valid)
        if pc is None:
            bad += 1
            continue
        missing += pc["missing"]
        dup += pc["dup"]
        nclust += len(pc["clusters"])
        sizes += [len(c) for c in pc["clusters"]]
        if ref_clusters is not None and key in ref_clusters:
            f1s.append(pair_f1(pc["clusters"], ref_clusters[key]))
    return {"groups": len(done), "bad_json": bad, "lat_p50": statistics.median(lat), "tin": tin, "tout": tout,
            "cost": cost(spec, tin, tout), "clusters": nclust, "cluster_ratio": nclust / max(1, total),
            "max_cluster": max(sizes, default=0), "missing_ids": missing, "dup_ids": dup,
            "pair_f1": statistics.mean(f1s) if f1s else None,
            "_ref": {k: parse_clusters(r, {x["id"] for x in groups[k]}) for k, r in done}}


# ---------- riport ----------

def fmt(v, kind="f2"):
    if v is None:
        return "n/a"
    return {"f2": f"{v:.2f}", "pct": f"{v * 100:.0f}%", "usd": f"${v:.4f}", "int": f"{int(v)}",
            "s": f"{v:.1f}s"}[kind]


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def cost_section(specs, ex, mg, judges, judge_spec):
    """Teljes költség: (a) a riport adataiban lévő összes hívás (gyorsítótárazott is), (b) ebben a futásban kifizetett."""
    def add(a, b):
        return None if a is None or b is None else a + b

    by_name = {s["name"]: s for s in specs}
    if judge_spec:
        by_name.setdefault(judge_spec["name"], judge_spec)
    rows, total = [], 0.0
    unknown = False
    for s in specs:
        n = s["name"]
        j = judges.get(n) if judges else None
        jc = cost(judge_spec, j["tin"], j["tout"]) if (j and judge_spec) else 0.0
        parts = [ex[n]["cost"], mg[n]["cost"] if n in mg else 0.0, jc]
        t = add(add(parts[0], parts[1]), parts[2])
        unknown |= t is None
        total += t or 0.0
        rows.append([n, *(fmt(p, "usd") for p in parts), fmt(t, "usd")])
    L = ["", "## Költség", "",
         "### A riport adatainak teljes költsége (az előző futásokból gyorsítótárazott hívásokkal együtt)", "",
         table(["modell", "kinyerés", "összevonás", "bíráló", "összesen"], rows),
         "", f"**Összesen: {fmt(total, 'usd')}**" + (" (hiányos: néhány modell ára ismeretlen)" if unknown else "")]
    fr, fresh_total, fresh_unknown = [], 0.0, False
    for n, (tin, tout, calls) in sorted(FRESH.items()):
        c = cost(by_name[n], tin, tout) if n in by_name else None
        fresh_unknown |= c is None
        fresh_total += c or 0.0
        fr.append([n, str(calls), str(tin), str(tout), fmt(c, "usd")])
    L += ["", "### Ebben a futásban ténylegesen kifizetett (új API-hívások, az újrapróbálkozással együtt)", ""]
    if fr:
        L += [table(["modell", "hívás", "bemeneti token", "kimeneti token", "költség"], fr), "",
              f"**Összesen: {fmt(fresh_total, 'usd')}**" + (" (hiányos: ismeretlen ár)" if fresh_unknown else "")]
    else:
        L += ["Nem volt új API-hívás, minden a gyorsítótárból jött: $0.0000"]
    L += ["", "A számok becslések a megadott listaárakból (`price`). A gondolkodó (reasoning) tokenek a kimenetben szerepelnek. "
              "A batch-kedvezményt és a gyorsítótárazott bemeneti árat nem veszik figyelembe."]
    return L


def report(specs, ex, mg, judges, n_sample, n_total, ref_name, judge_spec=None):
    L = [f"# Benchmark ({n_sample}/{n_total} magyarázó, referencia: {ref_name})", ""]
    L += ["## Kinyerés", ""]
    rows = []
    for s in specs:
        e = ex[s["name"]]
        full = None if e["cost"] is None else e["cost"] / n_sample * n_total
        rows.append([s["name"], fmt(e["lat_p50"], "s"), fmt(e["lat_p95"], "s"), fmt(e["cost"], "usd"),
                     fmt(full, "usd"), f"{e['errors']}/{e['json_fail']}/{e['truncated']}",
                     fmt(e["items_per_comm"]), fmt(e["unverified_rate"], "pct"), fmt(e["dup_rate"], "pct"),
                     fmt(e["foreign_char_rate"], "pct"), fmt(e["ctx_share"], "pct")])
    L.append(table(["modell", "p50", "p95", "költség (minta)", "becsült teljes", "hiba/JSON/csonka",
                    "tétel/magyarázó", "nem igazolt idézet", "ismétlődés", "idegen betű", "CTX arány"], rows))
    L += ["", "## Egyezés a referenciával (idézet-átfedés alapján)", ""]
    rows = [[s["name"], fmt(ex[s["name"]]["recall_vs_ref"], "pct"), fmt(ex[s["name"]]["precision_vs_ref"], "pct"),
             fmt(ex[s["name"]]["anchor_agree"], "pct")] for s in specs]
    L.append(table(["modell", "lefedettség", "pontosság", "horgony-egyezés"], rows))
    if judges:
        L += ["", "## LLM-bíró (1-5, vak; a bíró saját családját előnyben részesítheti!)", ""]
        rows = [[s["name"], *(fmt(judges[s["name"]][k]) if judges.get(s["name"]) and "fidelity" in judges[s["name"]] else "n/a"
                              for k in ("fidelity", "coverage", "anchor", "hungarian", "hallucinated")),
                 ] for s in specs]
        L.append(table(["modell", "hűség", "lefedettség", "horgony", "magyar", "kitalált tétel"], rows))
    if mg:
        L += ["", "## Összevonás (azonos bemeneten)", ""]
        rows = []
        for s in specs:
            m = mg[s["name"]]
            rows.append([s["name"], fmt(m["lat_p50"], "s"), fmt(m["cost"], "usd"), f"{m['bad_json']}/{m['groups']}",
                         str(m["missing_ids"]), str(m["dup_ids"]), fmt(m["cluster_ratio"]), str(m["max_cluster"]),
                         fmt(m["pair_f1"])])
        L.append(table(["modell", "p50", "költség", "hibás JSON/csoport", "kihagyott id", "duplikált id",
                        "klaszter/tétel", "max klaszter", "páros F1 a ref.-hez"], rows))
    L += cost_section(specs, ex, mg, judges, judge_spec)
    L += ["", "Megjegyzés: a referencia nem 'igazság', csak viszonyítási pont. A végső ítélethez nézz bele néhány "
              "kimenetbe is (out/bench/<modell>/extract/*.json), főleg a nem igazolt idézeteket."]
    return "\n".join(L) + "\n"


# ---------- fő folyamat ----------

def load_models(a):
    models = json.loads(Path(a.models).read_text(encoding="utf-8")) if a.models else DEFAULT_MODELS
    if a.only:
        want = [w.strip() for w in a.only.split(",")]
        models = [m for m in models if m["name"] in want]
    elif not a.include_optional:
        models = [m for m in models if not m.get("optional")]
    return models


async def main_async(a):
    models = load_models(a)
    if a.list:
        for m in load_models(argparse.Namespace(models=a.models, only=None, include_optional=True)):
            print(f"{m['name']:22} {m['provider']:10} {m['model']:24} ár={m.get('price')}"
                  f"{'  (optional)' if m.get('optional') else ''}")
        return
    if not models:
        sys.exit("Nincs kiválasztott modell.")
    global VERSE
    VERSE, links, vdir = sefaria.prepare(a)
    root = vdir / "bench"
    commentators = clean_links(links, a.lang, a.keep_nikud, anchor=VERSE.ref)
    print(f"Vers: {VERSE.ref}")
    sample = stratified_sample(commentators, a.sample, a.seed)
    print(f"Minta: {len(sample)}/{len(commentators)} magyarázó, {sum(c['chars'] for c in sample)} karakter")
    by_name = {m["name"]: m for m in models}
    ref_name = a.reference if a.reference in by_name else models[0]["name"]
    judge_spec = None
    if a.judge:
        judge_spec = by_name.get(a.judge) or next((m for m in load_models(argparse.Namespace(models=a.models, only=None, include_optional=True))
                                                   if m["name"] == a.judge), None)
        if not judge_spec:
            sys.exit(f"Ismeretlen bíró: {a.judge}")

    print("1/3 Kinyerés minden modellel (párhuzamosan)")
    raw = await asyncio.gather(*(run_extract(m, sample, a, root) for m in models))
    ex = {m["name"]: analyse_extract(m, d, sample) for m, d in zip(models, raw)}
    ref_items = ex[ref_name]["items"]
    for m in models:
        ex[m["name"]].update(compare_to_ref(ex[m["name"]]["items"], ref_items))

    judges = {}
    if judge_spec:
        print(f"2/3 Bíráló ({judge_spec['name']})")
        res = await asyncio.gather(*(run_judge(judge_spec, m, sample, ex[m["name"]]["items"], a, root) for m in models))
        judges = {m["name"]: r for m, r in zip(models, res)}

    mg = {}
    if not a.skip_merge:
        print("3/3 Összevonás a referencia-tételeken")
        groups = OrderedDict((k, [x for x in ref_items if x["anchor"] == k]) for k in ["CTX", *VERSE.segments])
        groups = OrderedDict(sorted(((k, g) for k, g in groups.items() if len(g) >= 4),
                                    key=lambda kv: -len(kv[1]))[:a.merge_groups])
        if not groups:
            print("  Nincs elég tétel az összevonási teszthez.")
        else:
            mraw = await asyncio.gather(*(run_merge(m, groups, a, root) for m in models))
            ref_idx = [m["name"] for m in models].index(ref_name)
            ref_cl = {k: (v["clusters"] if v else None) for k, v in
                      analyse_merge(models[ref_idx], mraw[ref_idx], groups, None)["_ref"].items()}
            ref_cl = {k: v for k, v in ref_cl.items() if v}
            for m, d in zip(models, mraw):
                mg[m["name"]] = analyse_merge(m, d, groups, ref_cl)

    md = report(models, ex, mg, judges, len(sample), len(commentators), ref_name, judge_spec)
    root.mkdir(parents=True, exist_ok=True)
    (root / "report.md").write_text(md, encoding="utf-8")
    print("\n" + md)
    print(f"Mentve: {root / 'report.md'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sefaria.add_args(ap)
    ap.add_argument("--models", help="JSON fájl a modellek listájával (lásd DEFAULT_MODELS)")
    ap.add_argument("--only", help="vesszővel elválasztott modellnevek")
    ap.add_argument("--reference", default="sonnet", help="referencia-modell neve (viszonyítási pont)")
    ap.add_argument("--judge", help="bíró-modell neve (opcionális, extra költség)")
    ap.add_argument("--sample", type=int, default=25)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--merge-groups", type=int, default=6, help="a legnagyobb ennyi horgonycsoporton fut az összevonás")
    ap.add_argument("--skip-merge", action="store_true")
    ap.add_argument("--concurrency", type=int, default=8, help="párhuzamos hívás modellenként")
    ap.add_argument("--extract-max-tokens", type=int, default=8000)
    ap.add_argument("--merge-max-tokens", type=int, default=8000)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--include-optional", action="store_true", help="az 'optional' (drága plafon) modelleket is futtatja")
    ap.add_argument("--list", action="store_true")
    asyncio.run(main_async(ap.parse_args()))
