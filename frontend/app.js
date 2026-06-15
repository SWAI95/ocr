"use strict";

const REGION_COLORS = {
  text: "#00aa00", title: "#dc0000", table: "#0000c8", figure: "#ff8c00",
  footnote: "#b400b4", header: "#787878", footer: "#787878", seal: "#ff0000",
  formula: "#0078c8", other: "#5a5a5a",
};

let MANIFEST = null;

function el(id) { return document.getElementById(id); }

async function loadEngines() {
  const res = await fetch("/api/engines");
  const data = await res.json();
  MANIFEST = data;
  const badge = el("offlineBadge");
  badge.textContent = data.offline ? "OFFLINE" : "ONLINE(dev)";
  badge.className = "badge " + (data.offline ? "offline" : "online");

  const sel = el("engine");
  sel.innerHTML = "";
  data.engines.forEach((e) => {
    const o = document.createElement("option");
    o.value = e.id;
    o.textContent = e.label + (e.available ? "" : " (미설치)");
    o.disabled = !e.available;
    sel.appendChild(o);
  });
  // 기본 엔진: hybrid_vl(5090·4090 둘 다 GPU) 우선, 미설치면 첫 사용가능 엔진
  const preferred = data.engines.find((e) => e.id === "hybrid_vl" && e.available);
  const firstAvail = preferred || data.engines.find((e) => e.available);
  if (firstAvail) sel.value = firstAvail.id;
  onEngineChange();
}

function fillStage(selectId, opts) {
  const sel = el(selectId);
  sel.innerHTML = "";
  if (!opts || opts.length === 0) {
    const o = document.createElement("option");
    o.value = ""; o.textContent = "(없음)";
    sel.appendChild(o);
    sel.disabled = true;
    return;
  }
  sel.disabled = false;
  opts.forEach((m) => {
    const o = document.createElement("option");
    o.value = m.id;
    o.textContent = m.id + (m.desc ? ` — ${m.desc}` : "");
    if (m.default) o.selected = true;
    sel.appendChild(o);
  });
}

function onEngineChange() {
  const eid = el("engine").value;
  const eng = MANIFEST.engines.find((e) => e.id === eid);
  if (!eng) return;
  el("engineLicense").textContent = "라이선스: " + (eng.license || "-");
  const st = eng.stages || {};
  fillStage("det_model", st.detection);
  fillStage("rec_model", st.recognition);
  fillStage("layout_model", st.layout);
  el("use_layout").disabled = !st.layout;
  if (!st.layout) el("use_layout").checked = false;
}

function renderLegend(regions) {
  const seen = new Set(regions.map((r) => r.label));
  const lg = el("legend");
  lg.innerHTML = "";
  seen.forEach((lbl) => {
    const s = document.createElement("span");
    s.innerHTML = `<i style="background:${REGION_COLORS[lbl] || "#5a5a5a"}"></i>${lbl}`;
    lg.appendChild(s);
  });
}

function renderMetrics(m, timings, device, meta) {
  const box = el("metrics");
  box.innerHTML = "";
  const add = (k, v, cls = "") => {
    const d = document.createElement("div");
    d.className = "metric " + cls;
    d.innerHTML = `<div class="v">${v}</div><div class="k">${k}</div>`;
    box.appendChild(d);
  };
  if (m) {
    const cerCls = m.cer <= 0.1 ? "good" : m.cer >= 0.3 ? "bad" : "";
    const werCls = m.wer <= 0.2 ? "good" : m.wer >= 0.5 ? "bad" : "";
    add("CER", (m.cer * 100).toFixed(1) + "%", cerCls);
    add("WER", (m.wer * 100).toFixed(1) + "%", werCls);
    add("정확도(문자)", (m.cer_accuracy * 100).toFixed(1) + "%");
    add("치환/삭제/삽입", `${m.char_substitutions}/${m.char_deletions}/${m.char_insertions}`);
  } else {
    add("CER", "—"); add("WER", "—");
  }
  const totalMs = Object.values(timings || {}).reduce((a, b) => a + b, 0);
  const t = document.createElement("div");
  t.className = "timings";
  t.textContent = `device: ${device} | ${Object.entries(timings || {})
    .map(([k, v]) => `${k}=${v}ms`).join(", ")} | total≈${totalMs.toFixed(0)}ms`
    + (meta ? ` | ${JSON.stringify(meta)}` : "");
  box.appendChild(t);
}

// 페이지별 오버레이를 세로로 누적
function renderPages(pages) {
  const box = el("pages");
  box.innerHTML = "";
  (pages || []).forEach((p) => {
    const card = document.createElement("div");
    card.className = "pagecard";
    card.innerHTML =
      `<div class="phead">${p.page + 1}페이지 · 줄 ${p.num_lines} · 영역 ${p.num_regions}</div>` +
      (p.overlay ? `<img class="pageimg" src="${p.overlay}" alt="${p.page + 1}페이지" />` : "");
    box.appendChild(card);
  });
}

// 인식 텍스트 — 페이지 구분선과 함께 이어붙여 표시
function renderTextPages(pages) {
  const box = el("text");
  box.innerHTML = "";
  (pages || []).forEach((p) => {
    const sep = document.createElement("div");
    sep.className = "pagesep";
    sep.textContent = `── ${p.page + 1}페이지 ──`;
    box.appendChild(sep);
    p.lines.forEach((l) => {
      const d = document.createElement("div");
      d.className = "line";
      const reg = l.region ? `<span class="reg">[${l.region}]</span>` : "";
      d.innerHTML = `${reg}${escapeHtml(l.text)} <span class="reg">${(l.score * 100).toFixed(0)}%</span>`;
      box.appendChild(d);
    });
  });
}

function escapeHtml(s) {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function renderTables(tables) {
  const wrap = el("tablesWrap");
  const box = el("tables");
  if (!tables || tables.length === 0) { wrap.style.display = "none"; box.innerHTML = ""; return; }
  wrap.style.display = "block";
  // pred_html 은 PaddleOCR가 만든 신뢰된 <table> 구조 (자체 생성, 외부 입력 아님)
  box.innerHTML = tables.map((t, i) =>
    `<div class="tbl"><div class="reg">표 ${i + 1}</div>${t.html || "(구조 없음)"}</div>`
  ).join("");
}

// 여러 페이지의 영역/표를 하나로 펼침
function flatRegions(pages) { return (pages || []).flatMap((p) => p.regions); }
function flatTables(pages) { return (pages || []).flatMap((p) => p.tables); }

async function run() {
  const f = el("file").files[0];
  if (!f) { setStatus("문서를 선택하세요."); return; }
  const btn = el("runBtn");
  btn.disabled = true;
  setStatus("실행 중... (최초 1회 모델 로드 수십 초 + 페이지 수만큼 소요)");

  const fd = new FormData();
  fd.append("file", f);
  fd.append("engine", el("engine").value);
  fd.append("lang", el("lang").value);
  fd.append("det_model", el("det_model").value);
  fd.append("rec_model", el("rec_model").value);
  fd.append("layout_model", el("layout_model").value);
  fd.append("use_layout", el("use_layout").checked);
  fd.append("use_table", el("use_table").checked);
  fd.append("use_seal", el("use_seal").checked);
  fd.append("use_gpu", el("use_gpu").checked);
  fd.append("ground_truth", el("ground_truth").value);
  const pp = [];
  if (el("pp_red").checked) pp.push("red_stamp");
  if (el("pp_flatten").checked) pp.push("flatten");
  if (el("pp_barcode").checked) pp.push("barcode");
  fd.append("preprocess", pp.join(","));

  const t0 = performance.now();
  try {
    const res = await fetch("/api/run", { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err));
    }
    const data = await res.json();
    el("compareWrap").style.display = "none";
    renderPages(data.pages);
    renderLegend(flatRegions(data.pages));
    renderMetrics(data.metrics, data.timings_ms, data.device, data.meta);
    renderTextPages(data.pages);
    renderTables(flatTables(data.pages));
    const wall = ((performance.now() - t0) / 1000).toFixed(1);
    setStatus(`완료: ${data.num_pages}페이지 / ${data.num_lines}줄 / 영역 ${data.num_regions}개 / ${wall}s`);
  } catch (e) {
    setStatus("오류: " + e.message);
  } finally {
    btn.disabled = false;
  }
}

async function compare() {
  const f = el("file").files[0];
  if (!f) { setStatus("이미지를 선택하세요."); return; }
  const avail = MANIFEST.engines.filter((e) => e.available).map((e) => e.id);
  if (avail.length === 0) { setStatus("설치된 엔진이 없습니다."); return; }

  const btn = el("compareBtn");
  btn.disabled = true;
  setStatus(`비교 실행 중: ${avail.join(", ")} (엔진별 모델 로드로 시간 소요)`);

  const fd = new FormData();
  fd.append("file", f);
  fd.append("engines", avail.join(","));
  fd.append("lang", el("lang").value);
  fd.append("use_layout", el("use_layout").checked);
  fd.append("use_table", el("use_table").checked);
  fd.append("use_gpu", el("use_gpu").checked);
  fd.append("ground_truth", el("ground_truth").value);

  try {
    const res = await fetch("/api/compare", { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err));
    }
    const data = await res.json();
    renderCompare(data.results);
    // 최고 성능(최저 CER) 엔진의 오버레이/텍스트/지표 표시 (정답 없으면 첫 성공 엔진)
    const best = bestResult(data.results);
    if (best) {
      renderPages(best.pages);
      renderLegend(flatRegions(best.pages));
      renderTextPages(best.pages);
      renderTables(flatTables(best.pages));
      renderMetrics(best.metrics, best.timings_ms, best.device, best.meta);
    }
    setStatus(`비교 완료: ${data.results.length}개 엔진 / ${data.num_pages}페이지`);
  } catch (e) {
    setStatus("오류: " + e.message);
  } finally {
    btn.disabled = false;
  }
}

// 최고 성능 결과 선택: 정답(metrics) 있으면 최저 CER, 없으면 첫 성공 엔진
function bestResult(results) {
  const ok = results.filter((r) => !r.error);
  const scored = ok.filter((r) => r.metrics && typeof r.metrics.cer === "number");
  if (scored.length) {
    return scored.reduce((a, b) => (b.metrics.cer < a.metrics.cer ? b : a));
  }
  return ok[0] || null;
}

function renderCompare(results) {
  el("compareWrap").style.display = "block";
  const t = el("compareTable");
  const hasGT = results.some((r) => r.metrics);
  // 성능순 정렬: CER 낮은 순(최고 성능 위) → 지표 없는 것 → 에러는 맨 아래
  const sorted = [...results].sort((a, b) => {
    if (a.error && !b.error) return 1;
    if (!a.error && b.error) return -1;
    const ca = a.metrics ? a.metrics.cer : Infinity;
    const cb = b.metrics ? b.metrics.cer : Infinity;
    return ca - cb;
  });
  // 최저 CER 엔진 강조(정렬 후 최상단)
  let bestCer = Infinity, bestIdx = -1;
  sorted.forEach((r, i) => {
    if (r.metrics && r.metrics.cer < bestCer) { bestCer = r.metrics.cer; bestIdx = i; }
  });
  let html = "<tr><th>엔진</th><th>device</th>" +
    (hasGT ? "<th>CER</th><th>WER</th>" : "") +
    "<th>줄</th><th>영역</th><th>시간(s)</th></tr>";
  sorted.forEach((r, i) => {
    if (r.error) {
      html += `<tr><td>${r.engine}</td><td class="err" colspan="${hasGT ? 5 : 3}">${escapeHtml(r.error)}</td></tr>`;
      return;
    }
    const cls = i === bestIdx ? ' class="best"' : "";
    const cer = r.metrics ? (r.metrics.cer * 100).toFixed(1) + "%" : "";
    const wer = r.metrics ? (r.metrics.wer * 100).toFixed(1) + "%" : "";
    html += `<tr${cls}><td>${r.engine}</td><td>${r.device}</td>` +
      (hasGT ? `<td>${cer}</td><td>${wer}</td>` : "") +
      `<td>${r.num_lines}</td><td>${r.num_regions}</td><td>${(r.total_ms / 1000).toFixed(1)}</td></tr>`;
  });
  t.innerHTML = html;
}

function setStatus(s) { el("status").textContent = s; }

// 정답 라벨 .txt 업로드 → 파일명 순(숫자 인식)으로 이어붙여 textarea 채움
async function loadGtFiles(fileList) {
  const files = [...fileList].sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { numeric: true }));
  if (files.length === 0) return;
  const texts = await Promise.all(files.map((f) => f.text()));
  el("ground_truth").value = texts.map((t) => t.replace(/\s+$/, "")).join("\n");
  setStatus(`라벨 ${files.length}개 불러옴: ${files.map((f) => f.name).join(", ")}`);
}

el("engine").addEventListener("change", onEngineChange);
el("gt_files").addEventListener("change", (e) => loadGtFiles(e.target.files));
el("runBtn").addEventListener("click", run);
el("compareBtn").addEventListener("click", compare);
loadEngines();
