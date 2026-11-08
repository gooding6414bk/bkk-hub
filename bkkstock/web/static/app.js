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
  async function loadNews(force) {
    const el = $("#news");
    if (!el) return;
    el.innerHTML = '<p class="muted">불러오는 중…</p>';
    try {
      const r = await call("GET", `/api/stock/${el.dataset.code}/news${force ? "?force=1" : ""}`);
      if (r.error) { el.innerHTML = `<p class="err small">${esc(r.error)}</p>`; return; }
      if (!r.items.length) { el.innerHTML = '<p class="empty">뉴스가 없습니다.</p>'; return; }
      const tone = { neg: '<span class="chip warn">위험 단어</span>', pos: '<span class="chip good">호재 단어</span>' };
      el.innerHTML = r.items.map((n) => `<div class="news-item"><a href="${esc(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a> ${tone[n.tone] || ""}<div class="meta">${esc(n.source || "")} · ${esc((n.published || "").replace("T", " ").slice(0, 16))}</div></div>`).join("")
        + `<p class="muted small">출처: ${r.source === "naver" ? "네이버 검색 API" : "구글 뉴스 RSS"} · ${esc((r.fetched_at || "").slice(11, 16))} 받음</p>`;
    } catch (err) { el.innerHTML = `<p class="err small">${esc(err.message)}</p>`; }
  }
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
    b.disabled = true;
    try { const r = await call("POST", `/api/jobs/${b.dataset.job}`); toast(r.message, !r.ok); }
    catch (err) { toast(err.message, true); }
    setTimeout(() => (b.disabled = false), 4000);
  }));
})();
