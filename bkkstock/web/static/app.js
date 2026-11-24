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
