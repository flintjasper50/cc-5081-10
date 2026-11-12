"""构筑演练图形入口（本地网页界面）。

启动后在浏览器里：
- 选择构筑（下拉框列出场景允许的构筑）
- 播放单场（逐步渲染战斗过程事件），运行由同一个 Kernel 现场执行
- 查看收益统计（由 rewards.compute_reward 按实际计数器结算）
- 核对战斗过程与统计：界面按事件流复算关键指标，与统计记录逐项比对，
  不一致会在页面顶部与统计区同时提示「异常」，不悄悄吞掉。

只用 Python 标准库（http.server），不依赖 tkinter，清洁目录即可启动。
"""

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import model
from .rewards import compute_reward
from .runner import run_single, verify_event_totals
from .view import _fmt_event

PRACTICE_SCENARIO = "battle_practice"


def build_payload(build_name, seed, scenario, data_dir=None):
    """运行单场并返回给界面的完整数据：事件、统计、收益、核对结果。"""
    _, specs = model.load_all(data_dir, [build_name], enemy=scenario.enemy)
    build, rules = specs[0]
    result = run_single(build, scenario.enemy, rules, seed=seed, trace=True,
                        scenario=scenario.name)
    events = []
    for entry in result.trace or []:
        if entry[0] != "event":
            continue
        _, t, kind, data = entry
        line = _fmt_event(kind, data)
        if line is None:
            continue
        events.append({"t": round(t, 2), "kind": kind, "text": line})

    ev = verify_event_totals(result)
    reward = compute_reward(result, build, scenario)
    c = result.counters
    stats = {
        "outcome": result.outcome.value,
        "completed": result.outcome.completed,
        "time": round(result.time, 2),
        "kill_time": None if result.kill_time is None else round(result.kill_time, 2),
        "damage_total": round(c["damage_total"], 1),
        "crits": c["crits"], "hits": c["hits"],
        "healing_done": round(c["healing_done"], 1),
        "damage_taken": round(c["damage_taken"], 1),
        "resource_spent": round(c["resource_spent"], 1),
        "starved_time": round(c["starved_time"], 1),
        "starved_events": c["starved_events"],
        "potions_used": c["potions_used"],
        "final_hp": round(c.get("final_hp", 0), 1),
        "enemy_max_hp": scenario.enemy.max_hp,
    }
    recomputed = ev["recomputed"]
    mismatch = []
    for key in ("damage_total", "healing_done", "damage_taken", "resource_spent",
                "starved_time", "starved_events", "crits", "hits", "potions_used"):
        a = stats[key]
        b = recomputed.get(key)
        if b is None:
            continue
        if abs(float(a) - float(b)) > 1e-3 * max(1.0, abs(float(b))):
            mismatch.append(f"{key}: 统计 {a} / 事件 {b}")
    return {
        "scenario": {"name": scenario.name, "label": scenario.label,
                     "enemy": scenario.enemy.name, "time_limit": scenario.enemy.time_limit},
        "build": {"name": build.name, "label": build.label},
        "seed": seed,
        "events": events,
        "stats": stats,
        "reward": {k: (None if v is None else round(v, 1)) for k, v in reward.items()
                   if k in ("base", "win_bonus", "speed_bonus", "damage_bonus", "hp_bonus", "total", "remaining_time", "hp_pct")},
        "reconciled": not mismatch,
        "mismatch": mismatch,
        "abnormal": (not result.outcome.completed),
        "abnormal_reason": result.error or result.stop_reason or "",
    }


def _scenario(data_dir=None):
    return model.load_scenario_by_name(PRACTICE_SCENARIO, data_dir)


def make_handler(data_dir=None):
    scenario = _scenario(data_dir)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                self._send_html(INDEX_HTML)
            elif u.path == "/api/scenario":
                self._send_json({
                    "scenario": scenario.label, "name": scenario.name,
                    "enemy": scenario.enemy.name,
                    "time_limit": scenario.enemy.time_limit,
                    "builds": [{"name": n, "label": model.load_build(
                        model.DATA_DIR / "builds" / f"{n}.json").label}
                        for n in scenario.builds],
                    "default_seed": scenario.seed,
                })
            elif u.path == "/api/play":
                q = parse_qs(u.query)
                build_name = (q.get("build") or ["burst"])[0]
                seed = int((q.get("seed") or [str(scenario.seed)])[0])
                try:
                    payload = build_payload(build_name, seed, scenario, data_dir)
                except Exception as exc:
                    self._send_json({"error": f"{type(exc).__name__}: {exc}"}, status=400)
                else:
                    self._send_json(payload)
            else:
                self.send_error(404)

        def _send_json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html):
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


def serve(host="127.0.0.1", port=8765, data_dir=None, open_browser=True):
    server = ThreadingHTTPServer((host, port), make_handler(data_dir))
    url = f"http://{host}:{port}/"
    print(f"构筑演练界面已启动: {url}  （Ctrl+C 停止）")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


INDEX_HTML = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8">
<title>构筑演练场</title>
<style>
 body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:#10141b;color:#dfe6f1}
 header{padding:14px 20px;background:#1a2130;border-bottom:1px solid #2c3648}
 h1{margin:0;font-size:18px} .sub{color:#8fa1bb;font-size:12px;margin-top:4px}
 main{padding:16px 20px;display:grid;grid-template-columns:340px 1fr;gap:16px}
 .card{background:#161c27;border:1px solid #2c3648;border-radius:8px;padding:14px}
 label{display:block;font-size:12px;color:#8fa1bb;margin:8px 0 4px}
 select,input,button{width:100%;padding:8px;border-radius:6px;border:1px solid #33405a;background:#0e131c;color:#dfe6f1;font-size:14px}
 button{background:#2d6cdf;border-color:#2d6cdf;margin-top:12px;cursor:pointer;font-weight:600}
 button:hover{background:#3a7cf0}
 .banner{padding:10px 12px;border-radius:6px;margin-bottom:12px;font-size:13px}
 .ok{background:#12351f;border:1px solid #1f6b3c} .bad{background:#3a1414;border:1px solid #8d2b2b}
 .kv{display:flex;justify-content:space-between;padding:3px 0;font-size:13px;border-bottom:1px dashed #232d3e}
 .kv b{color:#fff}
 .events{max-height:58vh;overflow:auto;font-family:ui-monospace,Menlo,monospace;font-size:12px}
 .events div{padding:2px 0;border-bottom:1px solid #1b2331}
 .muted{color:#7d8ea8}
 .pill{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;margin-left:6px}
</style>
<header>
 <h1>构筑演练场</h1>
 <div class="sub" id="sub">加载中…</div>
</header>
<main>
 <section class="card">
  <label>构筑</label><select id="build"></select>
  <label>种子</label><input id="seed" type="number" value="1" min="0">
  <button id="run">播放单场并核对</button>
  <div style="margin-top:16px;font-size:12px;color:#8fa1bb">
   收益统计由实际战斗计数器现场结算；上方横幅显示战斗过程事件与统计记录是否一致。
  </div>
 </section>
 <section>
  <div id="banner" class="banner ok" style="display:none"></div>
  <div style="display:grid;grid-template-columns:1fr 340px;gap:16px">
   <div class="card"><div style="font-size:13px;color:#8fa1bb;margin-bottom:8px">战斗过程（事件流）</div>
    <div class="events" id="events"></div></div>
   <div>
    <div class="card"><div style="font-size:13px;color:#8fa1bb;margin-bottom:8px">统计记录</div>
     <div id="stats"></div></div>
    <div class="card" style="margin-top:16px"><div style="font-size:13px;color:#8fa1bb;margin-bottom:8px">收益</div>
     <div id="reward"></div></div>
   </div>
  </div>
 </section>
</main>
<script>
let meta={};
async function init(){
 const r=await fetch('/api/scenario'); meta=await r.json();
 document.getElementById('sub').textContent=`场景 ${meta.scenario} · 对手 ${meta.enemy} · 时限 ${meta.time_limit}s`;
 const sel=document.getElementById('build');
 meta.builds.forEach(b=>{const o=document.createElement('option');o.value=b.name;o.textContent=`${b.label} (${b.name})`;sel.appendChild(o)});
 document.getElementById('seed').value=meta.default_seed;
 document.getElementById('run').onclick=play;
}
function kv(k,v){return `<div class="kv"><span class="muted">${k}</span><b>${v}</b></div>`}
async function play(){
 const b=document.getElementById('build').value,s=document.getElementById('seed').value;
 const banner=document.getElementById('banner');
 banner.style.display='none';
 const r=await fetch(`/api/play?build=${encodeURIComponent(b)}&seed=${encodeURIComponent(s)}`);
 const d=await r.json();
 if(d.error){banner.className='banner bad';banner.style.display='block';
  banner.textContent='异常：'+d.error;document.getElementById('events').innerHTML='';return;}
 const st=d.stats;
 const ev=document.getElementById('events');
 ev.innerHTML=d.events.map(e=>`<div><span class="muted">t+${e.t.toFixed(2)}s</span> ${e.text}</div>`).join('');
 document.getElementById('stats').innerHTML=[
  kv('结局',st.outcome),kv('是否完整结束',st.completed?'是':'否'),
  kv('用时',st.time.toFixed(2)+'s'),
  kv('击杀时间',st.kill_time==null?'-':st.kill_time.toFixed(2)+'s'),
  kv('总伤害',st.damage_total),kv('暴击',st.crits+'/'+st.hits),
  kv('治疗',st.healing_done),kv('承伤',st.damage_taken),
  kv('资源消耗',st.resource_spent),kv('资源受限',st.starved_time+'s / '+st.starved_events+'次'),
  kv('药水',st.potions_used),kv('剩余血量',st.final_hp),
 ].join('');
 const rw=d.reward;
 document.getElementById('reward').innerHTML=(rw.total==null)
  ?`<div class="kv"><span class="muted">收益</span><b>无可结算收益（非胜利或未完整结束）</b></div>`
  :[kv('合计',rw.total),kv('基础',rw.base),kv('胜利加成',rw.win_bonus),
     kv('速度加成',rw.speed_bonus),kv('伤害加成',rw.damage_bonus),kv('血量加成',rw.hp_bonus),
     kv('剩余时间',rw.remaining_time+'s'),kv('剩余血量比例',(rw.hp_pct*100).toFixed(1)+'%')].join('');
 if(d.reconciled && d.abnormal){
  banner.className='banner bad';banner.style.display='block';
  banner.textContent='异常：本场未完整结束（'+st.outcome+'）'+(d.abnormal_reason?(' → '+d.abnormal_reason):'')+'，收益不予结算。';}
 else if(d.reconciled){banner.className='banner ok';banner.style.display='block';
  banner.textContent='核对通过：战斗过程事件与统计记录逐项一致。';}
 else{banner.className='banner bad';banner.style.display='block';
  banner.textContent='异常：战斗过程与统计不一致 → '+d.mismatch.join('; ');}
}
init();
</script></html>
"""


def launch(data_dir=None, host="127.0.0.1", port=8765):
    try:
        return serve(host=host, port=port, data_dir=data_dir)
    except OSError as exc:
        print(f"无法启动图形界面（端口 {port} 被占用？）: {exc}")
        return 1
