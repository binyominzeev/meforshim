"""Önálló HTML a kérdés-térképről az out/<Könyv.fej.vers>/result-*.json fájlból.

Használat:  python viewer.py ["Genesis 17:17"] [--out out] [--tag TAG]
Kimenet: out/<Könyv.fej.vers>/kerdes-terkep.html (versenként egy; hivatkozás nélkül mindegyikre).
A magyarázók neve a Sefaria forrásszövegére linkel. Több eredmény esetén a legfrissebbet használja (vagy a --tag-et).
"""
import argparse
import glob
import json
import os

import sefaria

PAGE = r"""<!doctype html>
<html lang="hu"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__REF__ — kérdés-térkép</title>
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
.who{color:var(--acc);font-weight:600;text-decoration:none}
a.who:hover{text-decoration:underline}
.warn{color:var(--warn);font-weight:700}
.quote{direction:auto;unicode-bidi:plaintext;display:none;color:var(--mut);font-size:13px;margin-top:2px}
.showq .quote{display:block}
.hl{background:#ffe06655;border-radius:3px}
.hidden{display:none}
.ptr{font-size:14px;color:var(--mut)}
</style></head><body>
<header><h1>__REF__ — kérdés-térkép</h1>
<div class="verse" id="verse"></div></header>
<main>
<div class="bar">
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
const sefariaUrl=ref=>"https://www.sefaria.org/"+encodeURIComponent(ref.replace(/ /g,"_"));
$("verse").textContent=VERSE;
const whoLink=(name,ref)=>ref?`<a class="who" href="${esc(sefariaUrl(ref))}" target="_blank" rel="noopener" title="${esc(ref)}">${esc(name)}</a>`
  :`<span class="who">${esc(name)}</span>`;

function renderPanel(v){
  let h=`<div class="card panel">`;
  v.groups.forEach((g,gi)=>{
    h+=`<details class="l1" data-k="${gi}"><summary>${esc(g.title)}<span class="n group-count">${g.count}</span></summary>`;
    g.clusters.forEach((c,ci)=>{
      h+=`<details class="l2" data-k="${gi}.${ci}"><summary><span class="t">${esc(c.question)}</span>`+
      `${c.implicit?'<span class="tag">implicit</span>':""}<span class="n cluster-count">${c.n_commentators}</span></summary>`;
      c.answers.forEach(a=>{
        const who=whoLink(a.commentator,a.ref);
        h+=`<div class="l3" data-commentator="${esc(a.commentator)}"><span class="t">${esc(a.answer)}</span> (${who}`+
        `${a.verified===false?' <span class="warn" title="az idézet nem található a szövegben">⚠</span>':""})`+
        (a.quote?`<div class="quote">„${esc(a.quote)}"</div>`:"")+`</div>`;
      });
      h+="</details>";
    });
    h+="</details>";
  });
  if(v.pointers&&v.pointers.length){
    h+=`<details class="l1"><summary>Csak utalás (nem kérdés)<span class="n group-count">${v.pointers.length}</span></summary>`+
    v.pointers.map(p=>`<div class="l3 ptr">${whoLink(p.commentator,p.ref)}: ${esc(p.note)}</div>`).join("")+"</details>";
  }
  return h+"</div>";
}

function render(){
  $("panels").innerHTML=renderPanel(DATA);
  $("panels").classList.toggle("showq",$("q").checked);
  applySearch();
}

function applySearch(){
  const t=$("s").value.trim().toLowerCase();
  document.querySelectorAll("#panels .l1").forEach(group=>{
    const clusters=[...group.querySelectorAll(".l2")];
    if(clusters.length){
      let visibleCount=0;
      clusters.forEach(cluster=>{
        const questionMatches=!t||cluster.querySelector(".t").textContent.toLowerCase().includes(t);
        const answers=[...cluster.querySelectorAll(".l3")];
        answers.forEach(answer=>{
          answer.classList.toggle("hidden",!questionMatches&&t&&!answer.textContent.toLowerCase().includes(t));
        });
        const visibleAnswers=answers.filter(answer=>!answer.classList.contains("hidden"));
        const visible=questionMatches||visibleAnswers.length>0;
        cluster.classList.toggle("hidden",!visible);
        cluster.querySelector(".cluster-count").textContent=new Set(visibleAnswers.map(answer=>answer.dataset.commentator)).size;
        if(visible){
          visibleCount++;
          if(t)cluster.open=true;
        }
      });
      group.classList.toggle("hidden",!visibleCount);
      group.querySelector(".group-count").textContent=visibleCount;
    }else{
      const pointers=[...group.querySelectorAll(".l3")];
      pointers.forEach(pointer=>{
        pointer.classList.toggle("hidden",t&&!pointer.textContent.toLowerCase().includes(t));
      });
      const visibleCount=pointers.filter(pointer=>!pointer.classList.contains("hidden")).length;
      group.classList.toggle("hidden",!visibleCount);
      group.querySelector(".group-count").textContent=visibleCount;
    }
  });
}

$("q").onchange=()=>$("panels").classList.toggle("showq",$("q").checked);
$("s").oninput=applySearch;
$("exp").onclick=()=>document.querySelectorAll("#panels details").forEach(d=>d.open=true);
$("col").onclick=()=>document.querySelectorAll("#panels details").forEach(d=>d.open=false);
render();
</script></body></html>
"""


def build(vdir, tag=None):
    files = glob.glob(os.path.join(vdir, f"result-{tag}.json" if tag else "result-*.json"))
    if not files:
        return None
    data = json.load(open(max(files, key=os.path.getmtime), encoding="utf-8"))
    verse = json.load(open(os.path.join(vdir, "verse.json"), encoding="utf-8"))
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = (PAGE.replace("__DATA__", payload).replace("__VERSE__", json.dumps(verse["text"], ensure_ascii=False))
            .replace("__REF__", verse["ref"]))
    dest = os.path.join(vdir, "kerdes-terkep.html")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"{verse['ref']} -> {dest}")
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref", nargs="?", help='a vers, pl. "Genesis 17:17" (alap: minden vers, aminek van eredménye)')
    ap.add_argument("--out", default="out")
    ap.add_argument("--tag", help="melyik futás eredményét használja (alap: a legfrissebb)")
    a = ap.parse_args()
    if a.ref:
        book, ch, v = sefaria.parse_ref(a.ref)
        dirs = [os.path.join(a.out, f"{book}.{ch}.{v}")]
    else:
        dirs = sorted(os.path.dirname(p) for p in glob.glob(os.path.join(a.out, "*", "result-*.json")))
        dirs = list(dict.fromkeys(dirs))
    if not any([build(d, a.tag) for d in dirs]):
        raise SystemExit(f"Nincs eredmény ({a.out}/<vers>/result-*.json) — előbb futtasd a pipeline.py-t.")


if __name__ == "__main__":
    main()
