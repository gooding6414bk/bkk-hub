(() => {
  const csrf = document.querySelector('meta[name="csrf"]')?.content || "";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function toast(msg, bad = false) {
    const t = $("#toast");
    if (!t) return;
    t.textContent = msg;
    t.className = "show" + (bad ? " bad" : "");
    clearTimeout(t._h);
    t._h = setTimeout(() => (t.className = ""), 3500);
  }

  async function call(method, url, body) {
    const r = await fetch(url, {
      method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF": csrf },
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = {};
    try { data = await r.json(); } catch (e) { /* 빈 응답 */ }
    if (!r.ok) throw new Error(data.message || data.detail || `오류 ${r.status}`);
    return data;
  }


  // ---------- 바텀시트 · 화면 설정 (2026-11 디자인 v2) ----------
  function openSheet(title, bodyEl, onClose) {
    const scrim = document.createElement("div");
    scrim.className = "scrim";
    const sh = document.createElement("div");
    sh.className = "sheet";
    sh.setAttribute("role", "dialog");
    sh.setAttribute("aria-modal", "true");
    sh.innerHTML = `<div class="grab"></div><h3>${esc(title)}</h3>`;
    sh.appendChild(bodyEl);
    const close = () => { scrim.remove(); sh.remove(); document.removeEventListener("keydown", onKey); if (onClose) onClose(); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    scrim.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    document.body.append(scrim, sh);
    sh.querySelector("button, a")?.focus();
    return close;
  }
  async function savePrefs(body, msg) {
    try {
      await call("PUT", "/api/prefs", body);
      toast(msg || "저장했습니다");
      setTimeout(() => location.reload(), 350);
    } catch (err) { toast(err.message, true); }
  }
  // 정렬·보기 고르기: data-sheet / data-key("watch.sort") / data-opts / data-cur
  $$("[data-sheet]").forEach((b) => b.addEventListener("click", () => {
    const opts = JSON.parse(b.dataset.opts || "{}"), [grp, key] = b.dataset.key.split(".");
    const body = document.createElement("div");
    let close;
    Object.entries(opts).forEach(([v, label]) => {
      const o = document.createElement("button");
      o.type = "button";
      o.className = "opt" + (v === b.dataset.cur ? " on" : "");
      o.textContent = label;
      o.addEventListener("click", () => { close(); savePrefs({ [grp]: { [key]: v } }, `${label}(으)로 바꿨습니다`); });
      body.appendChild(o);
    });
    if (grp === "watch" && key === "sort") {
      const p = document.createElement("p");
      p.className = "small c3";
      p.textContent = "오늘 많이 오른 순 정렬은 일부러 넣지 않았습니다 (추격 매수 방지).";
      body.appendChild(p);
    }
    close = openSheet(key === "sort" ? "정렬" : "보기", body);
  }));
  // 홈 편집: 보이기/숨기기 + 위·아래 (드래그 없음)
  function editHome(btn) {
    let rows = JSON.parse(btn.dataset.widgets || "[]");
    const body = document.createElement("div");
    const draw = () => {
      body.innerHTML = rows.map((w, i) => `
        <div class="edit-row ${w.on ? "" : "off"} ${w.locked ? "locked" : ""}">
          <span class="tgl ${w.on ? "on" : ""}" role="checkbox" aria-checked="${w.on}" tabindex="0" data-i="${i}" aria-label="${esc(w.name)} 보이기"><i></i></span>
          <div><div class="nm">${esc(w.name)}${w.locked ? " 🔒" : ""}</div><div class="ds">${esc(w.desc)}</div></div>
          <button type="button" data-up="${i}" aria-label="${esc(w.name)} 위로" ${i === 0 ? "disabled" : ""}>↑</button>
          <button type="button" data-down="${i}" aria-label="${esc(w.name)} 아래로" ${i === rows.length - 1 ? "disabled" : ""}>↓</button>
        </div>`).join("") + `
        <p class="small c3">'오늘 볼 것'은 숨길 수 없고 위험이 있으면 늘 맨 위에 옵니다. '수집 상태'는 꺼 두어도 문제가 생기면 저절로 보입니다. 휴대폰과 PC가 같은 설정을 씁니다.</p>
        <button type="button" class="btn block" data-save>완료</button>
        <button type="button" class="btn ghost block" data-reset style="margin-top:8px">기본 배치로 되돌리기</button>`;
    };
    body.addEventListener("click", (e) => {
      const t = e.target.closest("[data-i],[data-up],[data-down],[data-save],[data-reset]");
      if (!t) return;
      if (t.dataset.i !== undefined) { const w = rows[+t.dataset.i]; if (!w.locked) w.on = !w.on; }
      else if (t.dataset.up !== undefined) { const i = +t.dataset.up; [rows[i - 1], rows[i]] = [rows[i], rows[i - 1]]; }
      else if (t.dataset.down !== undefined) { const i = +t.dataset.down; [rows[i + 1], rows[i]] = [rows[i], rows[i + 1]]; }
      else if (t.dataset.save !== undefined) { close(); return savePrefs({ home: rows.map((w) => [w.id, w.on]) }, "홈 화면을 저장했습니다"); }
      else if (t.dataset.reset !== undefined) { close(); return savePrefs({ reset: true }, "기본 배치로 되돌렸습니다"); }
      draw();
    });
    body.addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && e.target.dataset.i !== undefined) { e.preventDefault(); e.target.click(); } });
    draw();
    const close = openSheet("홈 화면 편집", body);
  }
  const editBtn = $("[data-edit-home]");
  if (editBtn) {
    editBtn.addEventListener("click", () => editHome(editBtn));
    if (new URLSearchParams(location.search).get("edit") === "1") editHome(editBtn);
  }


  // ---------- 규제 지도: 서울만 크게 / 서울+경기 ----------
  const rmap = document.querySelector(".regmap-wrap svg");
  if (rmap) {
    const full = rmap.getAttribute("viewBox"), seoul = rmap.dataset.seoulViewbox || full;
    const setView = (v) => {
      rmap.setAttribute("viewBox", v === "seoul" ? seoul : full);
      rmap.classList.toggle("zoom-seoul", v === "seoul");
      document.querySelectorAll("[data-map-view]").forEach((b) => b.classList.toggle("on", b.dataset.mapView === v));
    };
    document.querySelectorAll("[data-map-view]").forEach((b) => b.addEventListener("click", () => setView(b.dataset.mapView)));
    setView(window.innerWidth < 900 ? "seoul" : "all");
  }

  // ---------- 종목 검색·추가 ----------
  const form = $("#add-form");
  if (form) {
    const q = form.q, box = $("#suggest");
    let items = [], sel = -1, timer;
    const pick = (it) => {
      q.value = `${it.name} (${it.code})`;
      form.code.value = it.code;
      form.name.value = it.name;
      box.hidden = true;
    };
    const render = () => {
      box.innerHTML = items.map((it, i) =>
        `<li role="option" data-i="${i}" aria-selected="${i === sel}"><span>${esc(it.name)}</span><span class="muted small">${esc(it.code)} · ${esc(it.market || "")}</span></li>`).join("");
      box.hidden = !items.length;
    };
    q.addEventListener("input", () => {
      form.code.value = "";
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const v = q.value.trim();
        if (!v) { items = []; render(); return; }
        try { items = await call("GET", `/api/search?q=${encodeURIComponent(v)}`); } catch (e) { items = []; }
        sel = items.length ? 0 : -1;
        render();
      }, 150);
    });
    q.addEventListener("keydown", (e) => {
      if (box.hidden) return;
      if (e.key === "ArrowDown") { sel = Math.min(sel + 1, items.length - 1); render(); e.preventDefault(); }
      if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); render(); e.preventDefault(); }
      if (e.key === "Enter" && sel >= 0) { pick(items[sel]); e.preventDefault(); }
      if (e.key === "Escape") box.hidden = true;
    });
    box.addEventListener("mousedown", (e) => {
      const li = e.target.closest("li");
      if (li) { pick(items[+li.dataset.i]); e.preventDefault(); }
    });
    document.addEventListener("click", (e) => { if (!form.contains(e.target)) box.hidden = true; });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      let code = form.code.value;
      if (!code && /^\d{6}$/.test(q.value.trim())) code = q.value.trim();
      if (!code && items.length === 1) { pick(items[0]); code = items[0].code; }
      if (!code) { toast("목록에서 종목을 고르세요", true); return; }
      const btn = form.querySelector("button");
      btn.disabled = true;
      try {
        const r = await call("POST", "/api/watch", {
          code, name: form.name.value, kind: form.kind.value, qty: form.qty.value, avg_price: form.avg_price.value, memo: form.memo.value,
        });
        toast(`${r.name || r.code} 추가했습니다`);
        setTimeout(() => location.reload(), 500);
      } catch (err) { toast(err.message, true); btn.disabled = false; }
    });
  }

  $$("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`${b.dataset.name || b.dataset.del}을(를) 목록에서 뺄까요?`)) return;
    try { await call("DELETE", `/api/watch/${b.dataset.del}`); b.closest("tr").remove(); toast("삭제했습니다"); }
    catch (err) { toast(err.message, true); }
  }));

  $$("[data-watch]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try { await call("POST", "/api/watch", { code: b.dataset.watch, name: b.dataset.name, kind: "관심" }); toast("관심 종목에 추가했습니다"); setTimeout(() => location.reload(), 500); }
    catch (err) { toast(err.message, true); b.disabled = false; }
  }));

  // ---------- 뉴스 ----------
  const TONE = { pos: ["호재", "pos"], neg: ["악재", "neg"], mixed: ["섞임", "mixed"], neutral: ["중립", "info"], price: ["주가", "info"] };
  async function loadNews(force) {
    const el = $("#news");
    if (!el) return;
    el.innerHTML = '<p class="muted">불러오는 중…</p>';
    try {
      const r = await call("GET", `/api/stock/${el.dataset.code}/news${force ? "?force=1" : ""}`);
      if (r.error) { el.innerHTML = `<p class="err small">${esc(r.error)}</p>`; return; }
      if (!r.items.length) { el.innerHTML = '<p class="empty">뉴스가 없습니다.</p>'; return; }
      const sm = r.summary || {};
      // 비율 막대·주제 목록은 뺐다(2026-11 2차 점검: 기사 제목이 두 번 나오고, 매수 금지 종목에 '호재 %' 막대가 사는 이유처럼 읽힘)
      const items = r.items.map((n) => {
        const t = TONE[n.tone] || TONE.neutral;
        const why = (n.words || []).length ? `<span class="why">근거: ${n.words.map(esc).join(", ")}</span>` : "";
        const dup = n.dupes ? `<span class="muted small"> · 같은 소식 ${n.dupes}건 더</span>` : "";
        return `<div class="news-item"><span class="chip ${t[1]}">${t[0]}</span> ${/^https?:\/\//i.test(n.link || "") ? `<a href="${esc(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a>` : esc(n.title)}
          <div class="meta">${esc(n.source || "")} · ${esc((n.published || "").replace("T", " ").slice(0, 16))}${dup} ${why}</div></div>`;
      }).join("");
      const src = r.source === "naver" ? "네이버 검색 API" : r.source === "demo" ? "샘플" : "구글 뉴스 RSS";
      el.innerHTML = `<p class="small"><b>요약</b> ${esc(sm.summary || "")}</p>
        <h3>기사 ${sm.n || r.items.length}건 <span class="muted small">(원래 ${sm.raw_n || r.items.length}건, 같은 소식 묶음)</span></h3>
        ${items}<p class="muted small">출처: ${src} · ${esc((r.fetched_at || "").slice(11, 16))} 받음</p>`;
    } catch (err) { el.innerHTML = `<p class="err small">${esc(err.message)}</p>`; }
  }

  // ---------- 지식 계산기 (브라우저 안에서만 계산, 서버로 보내지 않음) ----------
  const manTxt = (v) => `${Math.round(v).toLocaleString()}만 원`;
  const fieldNum = (box, n) => Math.max(0, parseFloat((box.querySelector(`[name=${n}]`)?.value || "0").replace(/,/g, "")) || 0);
  const CALC = {
    pension(box) {
      const salary = fieldNum(box, "salary"), saving = fieldNum(box, "saving"), irp = fieldNum(box, "irp");
      const isa = fieldNum(box, "isa"), tax = fieldNum(box, "tax");
      if (!salary && !saving && !irp) return "";
      const base = Math.min(Math.min(saving, 600) + irp, 900);
      const extra = Math.min(isa * 0.1, 300);
      const rate = salary && salary <= 5500 ? 0.165 : 0.132;
      let refund = (base + extra) * rate;
      let note = "";
      if (tax && refund > tax * 1.1) { note = ` · 낸 세금이 적어 실제로는 최대 ${manTxt(tax * 1.1)}까지만 돌려받음`; refund = tax * 1.1; }
      const over = saving > 600 ? " · 연금저축 600만 원 넘는 부분은 공제 안 됨" : "";
      return `공제 대상 <b>${manTxt(base + extra)}</b> × ${(rate * 100).toFixed(1)}% = <b>${manTxt(refund)}</b> 환급${note}${over}`;
    },
    nps(box) {
      const age = fieldNum(box, "age") || 65, m = fieldNum(box, "monthly");
      const k = parseInt(box.querySelector("[name=shift]").value, 10);
      if (!m) return "";
      const factor = k < 0 ? 1 + 0.06 * k : 1 + 0.072 * k, start = age + k, mm = m * factor;
      const x = (mm * start - m * age) / (mm - m);     // 받은 돈 합계가 같아지는 나이
      const who = k < 0 ? "앞당긴 쪽" : "정상 나이 쪽";
      return `${start}세부터 월 <b>${manTxt(mm)}</b> (정상 ${age}세 월 ${manTxt(m)}) → 받은 돈 합계가 같아지는 나이 <b>${x.toFixed(1)}세</b>. 그 전에 세상을 떠나면 ${who}이 더 받음`;
    },
    ustax(box) {
      const gain = fieldNum(box, "gain"), loss = fieldNum(box, "loss"), net = gain - loss;
      if (!gain && !loss) return "";
      if (net <= 250) return `순이익 ${manTxt(net)} → 세금 없음. 공제 <b>${manTxt(250 - Math.max(net, 0))}</b> 남음 — 이만큼은 더 실현해도 비과세`;
      return `순이익 ${manTxt(net)} − 250만 = ${manTxt(net - 250)} × 22% = <b>${manTxt((net - 250) * 0.22)}</b> (다음 해 5월 신고)`;
    },
  };
  document.querySelectorAll("[data-calc]").forEach((box) => {
    const out = box.querySelector(".calc-out");
    const run = () => { out.innerHTML = CALC[box.dataset.calc](box) || '<span class="muted">숫자를 넣으면 바로 계산됩니다.</span>'; };
    box.addEventListener("input", run);
    box.addEventListener("change", run);
    run();
  });

  // ---------- 재무·배당 (OpenDART, 펼칠 때 아니라 화면 열 때 한 번) ----------
  const eokTxt = (v) => (v === null || v === undefined) ? "–" : (Math.abs(v) >= 1e12 ? `${(v / 1e12).toFixed(1)}조` : `${Math.round(v / 1e8).toLocaleString()}억`);
  const pctTxt = (v, d = 0) => (v === null || v === undefined) ? "–" : `${v.toFixed(d)}%`;
  async function loadFin() {
    const el = $("#fin"), dv = $("#divs");
    if (!el) return;
    try {
      const r = await call("GET", `/api/stock/${el.dataset.code}/fin`);
      if (r.error) { el.innerHTML = `<p class="empty">${esc(r.error)}</p>`; dv.innerHTML = '<p class="empty">–</p>'; return; }
      const warns = (r.warns || []).map((w) => `<span class="chip warn">⚠ ${esc(w)}</span>`).join(" ");
      const rows = r.years.map((y) => `<tr><td>${y.year}</td><td class="num">${eokTxt(y.sales)}</td><td class="num">${eokTxt(y.op)}</td><td class="num">${eokTxt(y.net)}</td><td class="num">${pctTxt(y.margin)}</td><td class="num">${pctTxt(y.debt)}</td><td class="num">${pctTxt(y.roe)}</td></tr>`).join("");
      const ytd = r.ytd ? `<p class="small">${esc(r.ytd.label)}: 매출 ${eokTxt((r.ytd.sales || [])[1])} (작년 같은 기간 ${eokTxt((r.ytd.sales || [])[0])}) · 영업이익 ${eokTxt((r.ytd.op || [])[1])} (${eokTxt((r.ytd.op || [])[0])})</p>` : "";
      el.innerHTML = `<p class="small"><b>${esc(r.summary || "")}</b> ${warns}</p>
        <div class="tbl-wrap"><table class="tbl compact fin-tbl"><thead><tr><th>연도</th><th class="r">매출</th><th class="r">영업이익</th><th class="r">순이익</th><th class="r">영업이익률</th><th class="r">부채비율</th><th class="r">ROE</th></tr></thead><tbody>${rows}</tbody></table></div>
        ${ytd}<p class="muted small">${esc(r.fs || "")} 재무 · ${r.demo ? "샘플" : "받은 날 " + esc(r.fetched || "")}${r.stale_error ? " · 새로 받기 실패: " + esc(r.stale_error) : ""}</p>`;
      const d = (r.div || []).filter((x) => x.dps !== null || x.payout !== null);
      dv.innerHTML = d.length ? `<div class="tbl-wrap"><table class="tbl compact"><thead><tr><th>연도</th><th class="r">주당 배당금</th><th class="r">배당성향</th><th class="r">배당수익률</th></tr></thead><tbody>${d.map((x) => `<tr><td>${x.year}</td><td class="num">${x.dps === null ? "–" : x.dps.toLocaleString() + "원"}</td><td class="num">${pctTxt(x.payout, 1)}</td><td class="num">${pctTxt(x.yield, 2)}</td></tr>`).join("")}</tbody></table></div><p class="muted small">배당성향 = 순이익 중 배당으로 준 비율. 배당수익률은 그해 기준 주가 대비.</p>` : '<p class="empty">배당 기록이 없습니다.</p>';
    } catch (err) { el.innerHTML = `<p class="err small">${esc(err.message)}</p>`; }
  }
  if ($("#fin")) loadFin();
  if ($("#news")) loadNews(false);
  $("#news-reload")?.addEventListener("click", () => loadNews(true));

  // ---------- API ----------
  $$("[data-test]").forEach((b) => b.addEventListener("click", async () => {
    const out = $(`.test-result[data-for="${b.dataset.test}"]`);
    b.disabled = true;
    out.className = "test-result small";
    out.textContent = "확인 중…";
    try {
      const r = await call("POST", `/api/apis/${b.dataset.test}/test`);
      out.textContent = r.message;
      out.classList.add(r.ok ? "ok" : "fail");
    } catch (err) { out.textContent = err.message; out.classList.add("fail"); }
    b.disabled = false;
  }));

  $$("form[data-keys]").forEach((f) => f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const values = {};
    $$("input", f).forEach((i) => { if (i.value.trim()) values[i.name] = i.value.trim(); });
    if (!Object.keys(values).length) { toast("바꿀 값을 입력하세요", true); return; }
    try {
      const r = await call("POST", `/api/apis/${f.dataset.keys}/keys`, { values });
      $$("input", f).forEach((i) => (i.value = ""));
      toast(`저장했습니다: ${r.changed.join(", ")}`);
      setTimeout(() => location.reload(), 800);
    } catch (err) { toast(err.message, true); }
  }));

  // ---------- 작업 실행 ----------
  $$("[data-job]").forEach((b) => b.addEventListener("click", async () => {
    if (b.dataset.confirm && !confirm(b.dataset.confirm)) return;
    b.disabled = true;
    try { const r = await call("POST", `/api/jobs/${b.dataset.job}`); toast(r.message, !r.ok); }
    catch (err) { toast(err.message, true); }
    setTimeout(() => (b.disabled = false), 4000);
  }));
})();
