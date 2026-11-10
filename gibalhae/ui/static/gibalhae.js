/* 영상 콘텐츠 관리 — 날짜별 상태·승인·제작·올리기 · 연결(API 키) · 작업 기록 */
"use strict";
const $ = (id) => document.getElementById(id);
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
const plainErr = window.plainErr || (() => "");

function toLogin() { location.href = "/login?next=" + encodeURIComponent(location.pathname + location.hash); }
async function getJSON(url, opt) {
  const r = await fetch(url, Object.assign({ cache: "no-store" }, opt));
  if (r.status === 401) { toLogin(); throw new Error("로그인이 필요합니다"); }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || r.statusText || String(r.status));
  return d;
}
const sendJSON = (url, method, body) => getJSON(url, {
  method, headers: { "Content-Type": "application/json" }, body: body == null ? undefined : JSON.stringify(body),
});
let toastTimer = 0;
function toast(msg, kind = "ok") {
  const t = $("adm-toast");
  t.className = "adm-toast " + kind; t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 6000);
}
const fmtTime = (t) => t ? new Date(t * 1000).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }) : "";
const ST = { draft: ["초안", "draft"], approved: ["승인", "approved"], built: ["영상 완성", "built"], published: ["올림", "published"], rejected: ["반려", "rejected"] };
const badge = (s) => s ? `<span class="gb-st ${ST[s] ? ST[s][1] : ""}">${esc(ST[s] ? ST[s][0] : s)}</span>` : `<span class="gb-st none">없음</span>`;

// ---------------------------------------------------------------- 콘텐츠
let overview = null, openDate = null;
async function loadOverview() {
  try {
    overview = await getJSON("/api/gibalhae/overview");
  } catch (e) { $("gb-days").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  $("gb-credits").textContent = overview.credits != null ? `JSON2Video 남은 크레딧 ${overview.credits}초 (≈ 쇼츠 ${Math.floor(overview.credits / 55)}편)` : "";
  const missing = Object.entries(overview.keys).filter(([k, v]) => !v.set && (k === "json2video" || k === "youtube_token")).length;
  $("gb-keys-badge").hidden = !missing; $("gb-keys-badge").textContent = missing;
  $("gb-jobs-badge").hidden = !overview.running; $("gb-jobs-badge").textContent = overview.running;
  const sel = $("gb-topic");
  sel.innerHTML = `<option value="">다음 소재(자동)</option>` + overview.topics.map((t) =>
    `<option value="${t.n}"${t.used ? " disabled" : ""}>${t.n}. [${esc(t.series)}] ${esc(t.title)}${t.used ? " · " + esc(t.used) : ""}</option>`).join("");
  if (!overview.days.length) { $("gb-days").innerHTML = `<div class="adm-note">아직 만든 날이 없습니다. 「새 날짜 만들기」 또는 아침 예약 작업을 기다리세요.</div>`; return; }
  $("gb-days").innerHTML = overview.days.map((d) => `
    <button type="button" class="gb-day${openDate === d.date ? " open" : ""}" data-date="${d.date}">
      <div class="gb-day-l"><b>${d.date}</b><span class="gb-title">${esc(d.title || "(제목 없음)")}</span>
        <span class="adm-note">${d.series ? "시리즈 " + esc(d.series) : ""}${d.topic ? " · 소재 " + d.topic : ""}</span></div>
      <div class="gb-day-r">
        <span class="gb-k">쇼츠</span>${badge(d.shorts && d.shorts.status)}${d.mp4 ? '<span class="gb-chip">mp4</span>' : ""}${d.shorts && d.shorts.youtube ? '<span class="gb-chip ok">유튜브</span>' : ""}
        <span class="gb-k">블로그</span>${badge(d.blog && d.blog.status)}${d.blog_bundle ? '<span class="gb-chip">묶음</span>' : ""}${d.blog && d.blog.url ? '<span class="gb-chip ok">올림</span>' : ""}
      </div></button>`).join("");
  $("gb-days").querySelectorAll(".gb-day").forEach((b) => b.addEventListener("click", () => openDay(b.dataset.date)));
  if (openDate) renderDetail(await getJSON(`/api/gibalhae/content/${openDate}`).catch(() => null));
}

async function openDay(date) {
  openDate = openDate === date ? null : date;
  $("gb-days").querySelectorAll(".gb-day").forEach((b) => b.classList.toggle("open", b.dataset.date === openDate));
  if (!openDate) { $("gb-detail").hidden = true; return; }
  try { renderDetail(await getJSON(`/api/gibalhae/content/${date}`)); } catch (e) { toast(e.message, "warn"); }
}

function checkLines(c) {
  if (!c) return "";
  const f = (c.fails || []).map((x) => `<div class="err">✗ ${esc(x)}</div>`).join("");
  const w = (c.warns || []).map((x) => `<div class="adm-note">△ ${esc(x)}</div>`).join("");
  return f + w || `<div class="adm-note ok">✓ 금지 표현 없음</div>`;
}

function renderDetail(d) {
  if (!d) { $("gb-detail").hidden = true; return; }
  const s = d.shorts, b = d.blog, bi = d.build;
  const ss = s ? s.status : null, bs = b ? b.status : null;
  const can = (st, ...ok) => ok.includes(st);
  const yt = s && s.meta.youtube || {};
  const act = [];
  if (can(ss, "draft", "rejected") || can(bs, "draft", "rejected")) {
    act.push(`<button class="adm-btn" data-act="approve">둘 다 승인</button>`);
    if (can(ss, "draft", "rejected")) act.push(`<button class="adm-btn" data-act="approve" data-args="--shorts">쇼츠만 승인</button>`);
    if (can(bs, "draft", "rejected")) act.push(`<button class="adm-btn" data-act="approve" data-args="--blog">블로그만 승인</button>`);
    act.push(`<button class="adm-btn danger" data-act="reject">반려(사유)</button>`);
  }
  if (can(ss, "approved")) act.push(`<button class="adm-btn primary" data-act="build">🎬 영상 만들기 (JSON2Video)</button>`);
  if (can(ss, "built")) act.push(`<button class="adm-btn primary" data-act="publish" data-args="--shorts">▶ 유튜브 예약 업로드</button>
    <button class="adm-btn" data-act="build">다시 만들기</button>`);
  if (can(bs, "approved")) act.push(`<button class="adm-btn primary" data-act="publish" data-args="--blog">📝 블로그 묶음 만들기${overview && overview.keys.wp_url && overview.keys.wp_url.set ? " / 워드프레스 올리기" : ""}</button>`);
  if (can(bs, "approved") && bi.blog_bundle) act.push(`<button class="adm-btn" data-act="published">네이버에 올린 주소 기록</button>`);
  act.push(`<button class="adm-btn sm" data-act="check">검수 다시</button>`);

  const video = bi.mp4 ? `<video class="gb-video" controls playsinline preload="metadata" src="/api/gibalhae/content/${d.date}/build/shorts.mp4"></video>
      <div class="adm-note">${s && s.meta.duration ? s.meta.duration + "초 · " : ""}${(bi.mp4_size / 1e6).toFixed(1)} MB${bi.j2v ? " · JSON2Video " + esc(bi.j2v.project || "") : ""}
      ${bi.j2v && bi.j2v.url ? ` · <a href="${esc(bi.j2v.url)}" target="_blank" rel="noopener">원본</a>` : ""} · <a href="/api/gibalhae/content/${d.date}/build/shorts.mp4" download>내려받기</a></div>`
    : `<div class="gb-empty">${ss === "approved" ? "승인됐습니다. 「영상 만들기」를 누르세요 (1~3분)" : ss === "draft" ? "승인하면 만들 수 있습니다" : "영상 없음"}</div>`;
  const imgs = (bi.blog_images || []).map((p) => `<a href="/api/gibalhae/content/${d.date}/build/${p}" target="_blank"><img src="/api/gibalhae/content/${d.date}/build/${p}" alt=""></a>`).join("");
  const bundle = bi.blog_bundle ? `<div class="gb-bundle"><b>네이버 붙여넣기 묶음</b> —
      <a href="/api/gibalhae/content/${d.date}/build/blog/CHECKLIST.md" target="_blank">순서(CHECKLIST)</a> ·
      <a href="/api/gibalhae/content/${d.date}/build/blog/post.txt" target="_blank">본문(post.txt)</a> ·
      <a href="/api/gibalhae/content/${d.date}/build/blog/post.html" target="_blank">HTML</a><div class="gb-imgs">${imgs}</div></div>` : "";

  $("gb-detail").hidden = false;
  $("gb-detail").innerHTML = `
    <div class="adm-sec-head"><h2>${d.date} · ${esc((s || b || {}).title || "")}</h2>
      <span class="adm-note">쇼츠 ${badge(ss)} 블로그 ${badge(bs)}</span></div>
    <div class="gb-actions" id="gb-actions">${act.join("")}</div>
    <div class="gb-cols">
      <div class="gb-col">
        <h3>🎬 쇼츠 ${yt.url ? `· <a href="${esc(yt.url)}" target="_blank" rel="noopener">${esc(yt.url)}</a>` : ""}</h3>
        ${video}
        ${s ? `<details class="gb-doc"><summary>대본 보기·고치기 ${checkLines(d.checks.shorts) ? "" : ""}</summary>
          ${checkLines(d.checks.shorts)}
          <textarea id="gb-edit-shorts" spellcheck="false"${can(ss, "published") ? " readonly" : ""}>${esc(d.raw.shorts)}</textarea>
          ${can(ss, "published") ? "" : `<button class="adm-btn sm" data-save="shorts">대본 저장</button>`}</details>` : `<div class="adm-note">대본 없음</div>`}
      </div>
      <div class="gb-col">
        <h3>📝 블로그 ${b && b.meta.published_url ? `· <a href="${esc(b.meta.published_url)}" target="_blank" rel="noopener">${esc(b.meta.published_url)}</a>` : ""}</h3>
        ${bundle || `<div class="gb-empty">${bs === "approved" ? "승인됐습니다. 「블로그 묶음 만들기」를 누르세요" : bs === "draft" ? "승인하면 묶음을 만듭니다" : "묶음 없음"}</div>`}
        ${b ? `<details class="gb-doc" open><summary>글 보기·고치기</summary>
          ${checkLines(d.checks.blog)}
          <textarea id="gb-edit-blog" spellcheck="false"${can(bs, "published") ? " readonly" : ""}>${esc(d.raw.blog)}</textarea>
          ${can(bs, "published") ? "" : `<button class="adm-btn sm" data-save="blog">글 저장</button>`}</details>` : `<div class="adm-note">글 없음</div>`}
      </div>
    </div>`;
  $("gb-detail").querySelectorAll("[data-act]").forEach((btn) => btn.addEventListener("click", () => runAction(btn.dataset.act, d.date, btn.dataset.args)));
  $("gb-detail").querySelectorAll("[data-save]").forEach((btn) => btn.addEventListener("click", () => saveDoc(d.date, btn.dataset.save)));
  if (!$("gb-detail").dataset.scrolled) { $("gb-detail").scrollIntoView({ behavior: "smooth", block: "start" }); }
}

async function saveDoc(date, kind) {
  const text = $(`gb-edit-${kind}`).value;
  try {
    const r = await sendJSON(`/api/gibalhae/content/${date}/${kind}`, "PUT", { text });
    toast(r.fails.length ? `저장했지만 금지 표현 ${r.fails.length}건: ${r.fails[0]}` : "저장했습니다" + (r.warns.length ? ` (경고 ${r.warns.length})` : ""), r.fails.length ? "warn" : "ok");
    renderDetail(await getJSON(`/api/gibalhae/content/${date}`));
  } catch (e) { toast(e.message, "warn"); }
}

async function runAction(cmd, date, argStr) {
  const args = argStr ? argStr.split(" ") : [];
  if (cmd === "reject") {
    const why = prompt("반려 사유 (클로드가 이 사유로 다시 씁니다)", "");
    if (why == null) return;
    args.push("--why", why.slice(0, 180));
  }
  if (cmd === "published") {
    const url = prompt("네이버에 올린 글 주소", "https://blog.naver.com/7284dh/");
    if (!url) return;
    args.push("--blog-url", url.trim());
  }
  if (cmd === "publish" && args.includes("--blog") && overview && overview.keys.wp_url && overview.keys.wp_url.set
      && confirm("워드프레스에 바로 올릴까요? (취소 = 네이버 붙여넣기 묶음만)")) args.push("--blog-target", "wordpress");
  if (cmd === "build" && !(overview && overview.keys.json2video && overview.keys.json2video.set) && !confirm("JSON2Video 키가 연결 탭에 없습니다. 그래도 시도할까요? (클라우드 세션이면 프록시가 넣어 줍니다)")) return;
  try {
    const r = await sendJSON("/api/gibalhae/action", "POST", { cmd, date, args });
    toast(`${cmdName(cmd)} 시작 — 작업 탭에서 진행을 봅니다`);
    watchJob(r.job.id, date);
  } catch (e) { toast(e.message, "warn"); }
}
const cmdName = (c) => ({ approve: "승인", reject: "반려", build: "영상 만들기", publish: "올리기", published: "주소 기록", check: "검수", new: "새 날짜" }[c] || c);

async function newDay() {
  const n = $("gb-topic").value;
  const date = prompt("날짜 (YYYY-MM-DD, 비우면 오늘)", "");
  if (date == null) return;
  const args = [];
  if (date.trim()) args.push("--date", date.trim());
  if (n) args.push("--topic", n);
  try {
    const r = await sendJSON("/api/gibalhae/action", "POST", { cmd: "new", args });
    toast("폴더를 만듭니다. 초안은 클로드 세션에서 「/daily-content draft」 로 채웁니다");
    watchJob(r.job.id, null);
  } catch (e) { toast(e.message, "warn"); }
}

// ---------------------------------------------------------------- 작업
const watching = new Set();
function watchJob(id, date) {
  if (watching.has(id)) return;
  watching.add(id);
  const tick = async () => {
    let j;
    try { j = await getJSON(`/api/gibalhae/jobs/${id}`); } catch (e) { watching.delete(id); return; }
    renderJobs(await getJSON("/api/gibalhae/jobs").catch(() => ({ jobs: [] })), id);
    if (j.status === "running") { setTimeout(tick, 2000); return; }
    watching.delete(id);
    toast(`${cmdName(j.name)} ${j.status === "ok" ? "끝남" : "실패 — 작업 탭 기록을 보세요"}`, j.status === "ok" ? "ok" : "warn");
    loadOverview();
  };
  tick();
}
let jobsOpen = null;
function renderJobs(d, openId) {
  const js = d.jobs || [];
  if (openId) jobsOpen = openId;
  if (!js.length) { $("gb-jobs").innerHTML = `<div class="adm-note">아직 없음</div>`; return; }
  $("gb-jobs").innerHTML = js.map((j) => `
    <details class="gb-job ${j.status}" data-id="${j.id}"${jobsOpen === j.id ? " open" : ""}>
      <summary><span class="gb-st ${j.status}">${j.status === "running" ? "진행 중" : j.status === "ok" ? "완료" : "실패"}</span>
        <b>${esc(cmdName(j.name))}</b> ${esc(j.date || "")} ${esc((j.args || []).join(" "))}
        <span class="adm-note">${fmtTime(j.started)}${j.ended ? " → " + fmtTime(j.ended) : ""} · ${esc(j.by)}</span></summary>
      <pre class="gb-log" id="gb-log-${j.id}">불러오는 중…</pre></details>`).join("");
  $("gb-jobs").querySelectorAll("details").forEach((el) => {
    const load = async () => { try { const j = await getJSON(`/api/gibalhae/jobs/${el.dataset.id}`); $(`gb-log-${el.dataset.id}`).textContent = j.log || "(기록 없음)"; } catch (e) { /* 무시 */ } };
    el.addEventListener("toggle", () => { if (el.open) { jobsOpen = el.dataset.id; load(); } });
    if (el.open) load();
  });
}
async function loadJobs() { try { renderJobs(await getJSON("/api/gibalhae/jobs")); } catch (e) { toast(e.message, "warn"); } }

// ---------------------------------------------------------------- 연결
async function loadKeys() {
  let d;
  try { d = await getJSON("/api/gibalhae/keys"); } catch (e) { $("gb-keys").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  $("gb-keys-file").textContent = d.file;
  const groups = [["json2video", "쇼츠 렌더 (필수)"], ["youtube_token", "유튜브 업로드 (필수)"], ["pexels", "배경 영상 (권장·무료)"],
    ["elevenlabs", "음성 (선택)"], ["wp", "워드프레스 (선택)"]];
  $("gb-keys").innerHTML = groups.map(([g, title]) => {
    const names = g === "wp" ? ["wp_url", "wp_user", "wp_app_password"] : [g];
    const checkName = g === "wp" ? "wordpress" : g;
    const anySet = names.some((n) => d.keys[n].set);
    return `<div class="gb-key${anySet ? " set" : ""}">
      <div class="gb-key-head"><b>${esc(title)}</b> <span class="gb-st ${anySet ? "ok" : "none"}">${anySet ? "설정됨" : "없음"}</span>
        <span class="adm-note">${esc(d.keys[names[0]].desc)}</span></div>
      ${names.map((n) => { const k = d.keys[n]; return `
        <label class="gb-field"><span>${esc(k.label)}${k.where ? ` <i class="adm-note">· ${esc(k.where)}</i>` : ""}</span>
          ${n === "youtube_token" ? `<textarea data-key="${n}" rows="3" placeholder="${k.set ? "저장됨 " + esc(k.hint) + " — 바꾸려면 붙여넣기" : '{"token": ..., "refresh_token": ..., "client_id": ...}'}"></textarea>`
            : `<input data-key="${n}" type="${k.secret ? "password" : "text"}" autocomplete="off" placeholder="${k.set ? "저장됨 " + esc(k.hint) + (k.source === "env" ? " (환경변수)" : "") + " — 바꾸려면 입력" : "붙여넣기"}">`}
        </label>`; }).join("")}
      <div class="gb-key-act"><button class="adm-btn sm" data-save-keys="${names.join(",")}">저장</button>
        <button class="adm-btn sm" data-check="${checkName}"${anySet ? "" : " disabled"}>확인</button>
        ${anySet ? `<button class="adm-btn sm danger" data-clear="${names.join(",")}">지우기</button>` : ""}
        <span class="gb-check" id="gb-check-${checkName}"></span></div></div>`;
  }).join("");
  $("gb-keys").querySelectorAll("[data-save-keys]").forEach((btn) => btn.addEventListener("click", async () => {
    const patch = {};
    btn.dataset.saveKeys.split(",").forEach((n) => { const el = $("gb-keys").querySelector(`[data-key="${n}"]`); if (el.value.trim()) patch[n] = el.value.trim(); });
    if (!Object.keys(patch).length) { toast("입력한 값이 없습니다", "warn"); return; }
    try { await sendJSON("/api/gibalhae/keys", "POST", patch); toast("저장했습니다 — 「확인」으로 실제 연결을 봅니다"); await loadKeys(); loadOverview(); }
    catch (e) { toast(e.message, "warn"); }
  }));
  $("gb-keys").querySelectorAll("[data-clear]").forEach((btn) => btn.addEventListener("click", async () => {
    if (!confirm("지울까요?")) return;
    const patch = {}; btn.dataset.clear.split(",").forEach((n) => { patch[n] = ""; });
    try { await sendJSON("/api/gibalhae/keys", "POST", patch); toast("지웠습니다"); await loadKeys(); loadOverview(); } catch (e) { toast(e.message, "warn"); }
  }));
  $("gb-keys").querySelectorAll("[data-check]").forEach((btn) => btn.addEventListener("click", async () => {
    const out = $(`gb-check-${btn.dataset.check}`);
    out.className = "gb-check"; out.textContent = "확인 중…"; btn.disabled = true;
    try { const r = await sendJSON(`/api/gibalhae/keys/${btn.dataset.check}/check`, "POST"); out.className = "gb-check " + (r.ok ? "ok" : "bad"); out.textContent = (r.ok ? "✓ " : "✗ ") + r.detail + (plainErr(r.detail) ? " — " + plainErr(r.detail) : ""); }
    catch (e) { out.className = "gb-check bad"; out.textContent = "✗ " + e.message; }
    btn.disabled = false;
  }));
}

// ---------------------------------------------------------------- 탭
const TABS = ["content", "keys", "jobs"];
function showTab(tab) {
  TABS.forEach((t) => { $(`tab-${t}`).hidden = t !== tab; });
  document.querySelectorAll(".adm-tab").forEach((b) => { const on = b.dataset.tab === tab; b.classList.toggle("active", on); b.setAttribute("aria-selected", on); });
  location.hash = tab;
  if (tab === "keys") loadKeys();
  else if (tab === "jobs") loadJobs();
  else loadOverview();
}
document.querySelectorAll(".adm-tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
$("gb-reload").addEventListener("click", () => showTab((location.hash || "#content").slice(1)));
$("gb-new").addEventListener("click", newDay);
showTab(TABS.includes((location.hash || "").slice(1)) ? location.hash.slice(1) : "content");
