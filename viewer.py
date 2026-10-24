"""Önálló HTML-összehasonlító az out/<Könyv.fej.vers>/result-*.json fájlokból.

Használat:  python viewer.py ["Genesis 17:17"] [--out out]
Kimenet: out/<Könyv.fej.vers>/compare.html (versenként egy; hivatkozás nélkül mindegyikre).
"""
import argparse
import glob
import json
import os

import sefaria

PAGE = r"""<!doctype html>
<html lang="hu"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__REF__ — kérdés-térkép összehasonlítás</title>
<style>
:root{--bg:#f6f5f1;--card:#fff;--fg:#1d1d1f;--mut:#6b6b70;--line:#e2e0d8;--acc:#2f5fd0;--ok:#1f7a45;--warn:#b86e00;--chip:#eceae2}
@media(prefers-color-scheme:dark){:root{--bg:#16171a;--card:#1f2024;--fg:#e8e8ea;--mut:#9a9aa2;--line:#33343a;--acc:#7da2ff;--ok:#5ccf8b;--warn:#f0b04a;--chip:#2b2c32}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
header{padding:20px 28px 8px}
h1{margin:0 0 4px;font-size:22px}
.verse{direction:rtl;font-size:24px;font-family:"SBL Hebrew","Ezra SIL",serif;margin:8px 0;color:var(--mut)}
main{padding:0 28px 40px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 18px;margin:14px 0}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:7px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
tfoot td{font-weight:700;border-bottom:0;font-size:15px}
td.big{font-weight:700;color:var(--acc)}
.note{color:var(--mut);font-size:13px;margin-top:8px}
.bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:14px 0}
select,input[type=search],button{font:inherit;padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg)}
button{cursor:pointer}button:hover{border-color:var(--acc)}
.panels{display:grid;gap:16px;grid-template-columns:1fr}
.panels.two{grid-template-columns:1fr 1fr}
@media(max-width:900px){.panels.two{grid-template-columns:1fr}}
.panel h2{font-size:16px;margin:0 0 4px}
.panel .sub{color:var(--mut);font-size:13px;margin-bottom:10px}
details{margin:2px 0}
summary{cursor:pointer;padding:4px 6px;border-radius:6px;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"▸";display:inline-block;width:1.1em;color:var(--mut);transition:transform .1s}
details[open]>summary::before{transform:rotate(90deg)}
summary:hover{background:var(--chip)}
.l1>summary{font-weight:700;font-size:16px}
.l2{margin-left:18px}.l2>summary{font-weight:500}
.l3{margin:2px 0 2px 38px;padding:3px 8px;border-left:3px solid var(--line)}
.n{display:inline-block;min-width:1.7em;text-align:center;padding:0 7px;border-radius:99px;background:var(--chip);color:var(--mut);font-size:12px;margin-left:6px}
.tag{font-size:11px;padding:1px 6px;border-radius:5px;background:var(--chip);color:var(--mut);margin-left:6px}
.who{color:var(--acc);font-weight:600}
.warn{color:var(--warn);font-weight:700}
.quote{direction:auto;unicode-bidi:plaintext;display:none;color:var(--mut);font-size:13px;margin-top:2px}
.showq .quote{display:block}
.hl{background:#ffe06655;border-radius:3px}
.hidden{display:none}
.ptr{font-size:14px;color:var(--mut)}
</style></head><body>
<header><h1>__REF__ — kérdés-térkép összehasonlítás</h1>
<div class="verse" id="verse"></div></header>
<main>
<div class="card"><h3 style="margin-top:0">Költség és futás</h3>
<table id="costs"></table><div class="note" id="costnote"></div></div>
<div class="bar">
<label>Bal: <select id="selA"></select></label>
<label>Jobb: <select id="selB"></select></label>
<button id="exp">Mind kinyit</button><button id="col">Mind összecsuk</button>
<label><input type="checkbox" id="q"> idézetek</label>
<input type="search" id="s" placeholder="Keresés…">
</div>
<div class="panels" id="panels"></div>
</main>
<script>
const DATA=__DATA__, VERSE=__VERSE__;
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const usd=(x,est)=>(est?"≈ ":"")+"$"+x.toFixed(4);
$("verse").textContent=VERSE;

function renderCosts(){
  let h="<thead><tr><th>Variáns</th><th>Kinyerés</th><th>Összevonás</th><th>Kinyerés $</th><th>Összevonás $</th><th>Összesen $</th><th>Idő</th><th>Kérdés</th><th>Nem igazolt</th></tr></thead><tbody>";
  let sum=0,anyEst=false;
  for(const v of DATA){
    const m=v.meta,c=m.cost,est=m.estimated;sum+=c.total;anyEst=anyEst||est;
    h+=`<tr><td>${esc(m.tag)}</td><td>${esc(m.extract_model)}</td><td>${esc(m.merge_model)}</td>`+
    `<td>${usd(c.extract,est)}</td><td>${usd(c.merge,est)}</td><td class="big">${usd(c.total,est)}</td>`+
    `<td>${m.seconds?m.seconds.total.toFixed(0)+" s":"—"}</td><td>${m.stats.questions}</td>`+
    `<td>${m.stats.unverified==null?"—":m.stats.unverified}</td></tr>`;
  }
  h+=`</tbody><tfoot><tr><td colspan="5">Végösszeg (minden variáns)</td><td class="big">${usd(sum,anyEst)}</td><td colspan="3"></td></tr></tfoot>`;
  $("costs").innerHTML=h;
  $("costnote").innerHTML=DATA.filter(v=>v.meta.note).map(v=>"<b>"+esc(v.meta.tag)+":</b> "+esc(v.meta.note)).join("<br>");
}

function renderPanel(v,side){
  const m=v.meta;let h=`<div class="card panel" data-side="${side}"><h2>${esc(m.tag)}</h2>`+
  `<div class="sub">${esc(m.extract_model)} → ${esc(m.merge_model)} · ${usd(m.cost.total,m.estimated)}</div>`;
  v.groups.forEach((g,gi)=>{
    h+=`<details class="l1" data-k="${gi}"><summary>${esc(g.title)}<span class="n">${g.count}</span></summary>`;
    g.clusters.forEach((c,ci)=>{
      h+=`<details class="l2" data-k="${gi}.${ci}"><summary><span class="t">${esc(c.question)}</span>`+
      `${c.implicit?'<span class="tag">implicit</span>':""}<span class="n">${c.n_commentators}</span></summary>`;
      c.answers.forEach(a=>{
        h+=`<div class="l3"><span class="t">${esc(a.answer)}</span> (<span class="who">${esc(a.commentator)}</span>`+
        `${a.verified===false?' <span class="warn" title="az idézet nem található a szövegben">⚠</span>':""})`+
        (a.quote?`<div class="quote">„${esc(a.quote)}"</div>`:"")+`</div>`;
      });
      h+="</details>";
    });
    h+="</details>";
  });
  if(v.pointers&&v.pointers.length){
    h+=`<details class="l1"><summary>Csak utalás (nem kérdés)<span class="n">${v.pointers.length}</span></summary>`+
    v.pointers.map(p=>`<div class="l3 ptr"><span class="who">${esc(p.commentator)}</span>: ${esc(p.note)}</div>`).join("")+"</details>";
  }
  return h+"</div>";
}

function render(){
  const a=+$("selA").value,b=$("selB").value;
  const two=b!=="";
  const p=$("panels");p.className="panels"+(two?" two":"");
  p.innerHTML=renderPanel(DATA[a],"A")+(two?renderPanel(DATA[+b],"B"):"");
  p.classList.toggle("showq",$("q").checked);
  applySearch();
}

function applySearch(){
  const t=$("s").value.trim().toLowerCase();
  document.querySelectorAll("#panels .l3").forEach(e=>{
    e.classList.toggle("hidden",t&&!e.textContent.toLowerCase().includes(t));
  });
  document.querySelectorAll("#panels .l2").forEach(d=>{
    const vis=!t||d.querySelector(".t").textContent.toLowerCase().includes(t)||[...d.querySelectorAll(".l3")].some(x=>!x.classList.contains("hidden"));
    d.classList.toggle("hidden",!vis);
    if(t&&vis)d.open=true;
  });
}

let syncing=false;
$("panels").addEventListener("toggle",e=>{
  if(syncing||!$("panels").classList.contains("two"))return;
  const d=e.target;if(!d.dataset||d.dataset.k===undefined)return;
  syncing=true;
  document.querySelectorAll(`#panels details[data-k="${d.dataset.k}"]`).forEach(o=>{if(o!==d)o.open=d.open});
  syncing=false;
},true);

DATA.forEach((v,i)=>{
  for(const id of ["selA","selB"]){
    const o=document.createElement("option");o.value=i;o.textContent=v.meta.tag;$(id).appendChild(o);
  }
});
const none=document.createElement("option");none.value="";none.textContent="— nincs (egy panel) —";$("selB").prepend(none);
$("selB").value=DATA.length>1?"1":"";
$("selA").onchange=$("selB").onchange=render;
$("q").onchange=()=>$("panels").classList.toggle("showq",$("q").checked);
$("s").oninput=applySearch;
$("exp").onclick=()=>document.querySelectorAll("#panels details").forEach(d=>d.open=true);
$("col").onclick=()=>document.querySelectorAll("#panels details").forEach(d=>d.open=false);
renderCosts();render();
</script></body></html>
"""


def build(vdir):
    files = sorted(glob.glob(os.path.join(vdir, "result-*.json")))
    if not files:
        return None
    data = [json.load(open(f, encoding="utf-8")) for f in files]
    data.sort(key=lambda r: r["meta"]["cost"]["total"])
    verse = json.load(open(os.path.join(vdir, "verse.json"), encoding="utf-8"))
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = (PAGE.replace("__DATA__", payload).replace("__VERSE__", json.dumps(verse["text"], ensure_ascii=False))
            .replace("__REF__", verse["ref"]))
    dest = os.path.join(vdir, "compare.html")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(html)
    total = sum(r["meta"]["cost"]["total"] for r in data)
    print(f"{verse['ref']}: {len(data)} variáns -> {dest} (végösszeg ${total:.4f})")
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref", nargs="?", help='a vers, pl. "Genesis 17:17" (alap: minden vers, aminek van eredménye)')
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    if a.ref:
        book, ch, v = sefaria.parse_ref(a.ref)
        dirs = [os.path.join(a.out, f"{book}.{ch}.{v}")]
    else:
        dirs = sorted(os.path.dirname(p) for p in glob.glob(os.path.join(a.out, "*", "result-*.json")))
        dirs = list(dict.fromkeys(dirs))
    if not any(build(d) for d in dirs):
        raise SystemExit(f"Nincs eredmény ({a.out}/<vers>/result-*.json) — előbb futtasd a pipeline.py-t.")


if __name__ == "__main__":
    main()
