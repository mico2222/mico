// 쇼츠자동화 화면 로직 — 프레임워크 없이 순수 JS.

async function api(path, opts) {
  const res = await fetch(path, opts);
  let body;
  try { body = await res.json(); } catch (e) { body = { ok: false, message: "서버 응답을 읽지 못했습니다." }; }
  if (!body.ok) throw new Error(body.message || "알 수 없는 오류가 발생했습니다.");
  return body.data;
}

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function showTab(name) {
  document.querySelectorAll("header nav button").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  ["setup", "new", "jobs", "job-detail"].forEach(n => {
    document.getElementById("tab-" + n).classList.toggle("hidden", n !== name);
  });
}

document.querySelectorAll("header nav button").forEach(b => {
  b.addEventListener("click", () => {
    showTab(b.dataset.tab);
    if (b.dataset.tab === "setup") renderSetup();
    if (b.dataset.tab === "new") renderNew();
    if (b.dataset.tab === "jobs") renderJobs();
  });
});

// ------------------------------------------------------------------
// 설치 상태
// ------------------------------------------------------------------
async function renderSetup() {
  const root = document.getElementById("tab-setup");
  root.innerHTML = `<div class="card"><p>불러오는 중...</p></div>`;
  let data;
  try { data = await api("/api/setup-status"); } catch (e) {
    root.innerHTML = `<div class="card error-box">${e.message}</div>`;
    return;
  }

  document.getElementById("build-badge").textContent =
    data.config_file_mtime ? "설정 저장 시각: " + new Date(data.config_file_mtime * 1000).toLocaleString() : "";

  const line = (label, ok, detail) =>
    `<div class="status-line ${ok ? "status-ok" : "status-fail"}">${ok ? "✔" : "✘"} ${label}${detail ? " — " + detail : ""}</div>`;

  const pkgLines = Object.entries(data.packages)
    .map(([k, v]) => line(k, v, v ? "" : "설치 필요")).join("");

  root.innerHTML = `
    <div class="card">
      <h3>0단계 준비물 확인</h3>
      ${line("파이썬 3.10 이상", data.python_ok, data.python_version)}
      ${line("ffmpeg", data.ffmpeg)}
      ${line("ffprobe", data.ffprobe)}
      ${line("클로드 코드(claude) 명령", data.claude_cli)}
      ${line("캡컷 초안 폴더", !!data.capcut_draft_folder, data.capcut_draft_folder || "못 찾음 (config.yaml 에서 직접 지정하세요)")}
      ${line("구글 키 파일", data.google_key_exists, data.google_key_path || "env.txt 에 없음")}
      <h4 style="margin-top:16px;">파이썬 꾸러미</h4>
      ${pkgLines}
      <div class="row" style="margin-top:14px;">
        <button class="primary" id="btn-install">도구 설치</button>
        <button class="secondary" id="btn-recheck">다시 확인</button>
      </div>
      <div id="install-log"></div>
    </div>`;

  document.getElementById("btn-recheck").addEventListener("click", renderSetup);
  document.getElementById("btn-install").addEventListener("click", async () => {
    const logBox = document.getElementById("install-log");
    logBox.innerHTML = `<p>설치 중입니다. 몇 분 걸릴 수 있어요...</p>`;
    try {
      await api("/api/install-tools", { method: "POST" });
      logBox.innerHTML = `<div class="status-line status-ok">설치가 끝났습니다.</div>`;
      renderSetup();
    } catch (e) {
      logBox.innerHTML = `<div class="error-box">${e.message}</div>`;
    }
  });
}

// ------------------------------------------------------------------
// 새 작업 (대본 입력)
// ------------------------------------------------------------------
function renderNew() {
  const root = document.getElementById("tab-new");
  root.innerHTML = `
    <div class="card">
      <h3>대본을 붙여넣으세요</h3>
      <p class="scene-note" style="height:auto;">전체 350~420자, 문장 8~13개, 첫 문장은 20자 이내를 권장합니다. 숫자는 "30%" 대신 "삼십 퍼센트"처럼 읽는 대로 적어주세요.</p>
      <textarea id="script-text" placeholder="여기에 대본을 붙여넣으세요..."></textarea>
      <div id="validate-box"></div>
      <div class="row" style="margin-top:12px;">
        <button class="primary" id="btn-create" disabled>만들기</button>
        <span id="create-status"></span>
      </div>
    </div>`;

  const ta = document.getElementById("script-text");
  const vbox = document.getElementById("validate-box");
  const btn = document.getElementById("btn-create");
  let timer = null;

  async function doValidate() {
    const text = ta.value;
    if (!text.trim()) { vbox.innerHTML = ""; btn.disabled = true; return; }
    let v;
    try { v = await api("/api/validate-script", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) }); }
    catch (e) { vbox.innerHTML = `<div class="error-box">${e.message}</div>`; return; }

    let html = `<p class="scene-note" style="height:auto;">글자 수 ${v.char_count} · 문장 ${v.sentence_count}개 · 예상 길이 약 ${v.est_seconds}초</p>`;
    if (v.errors.length) html += `<div class="error-box">${v.errors.join("\n")}</div>`;
    if (v.warnings.length) html += `<div class="warn-box">${v.warnings.join("\n")}</div>`;
    vbox.innerHTML = html;
    btn.disabled = !v.ok;
  }

  ta.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(doValidate, 400); });

  btn.addEventListener("click", async () => {
    btn.disabled = true;
    document.getElementById("create-status").textContent = "목소리·정렬·씬 분할을 준비하는 중입니다...";
    try {
      const data = await api("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: ta.value }) });
      showTab("job-detail");
      renderJobDetail(data.job_id);
    } catch (e) {
      document.getElementById("create-status").innerHTML = `<div class="error-box">${e.message}</div>`;
      btn.disabled = false;
    }
  });
}

// ------------------------------------------------------------------
// 작업 목록
// ------------------------------------------------------------------
async function renderJobs() {
  const root = document.getElementById("tab-jobs");
  const ids = await api("/api/jobs");
  if (!ids.length) { root.innerHTML = `<div class="card">아직 만든 작업이 없습니다.</div>`; return; }
  root.innerHTML = `<div class="card"><h3>작업 목록</h3>${ids.map(id =>
    `<div class="row" style="margin-bottom:8px;"><a href="#" data-id="${id}">${id}</a></div>`).join("")}</div>`;
  root.querySelectorAll("a[data-id]").forEach(a => a.addEventListener("click", (ev) => {
    ev.preventDefault();
    showTab("job-detail");
    renderJobDetail(a.dataset.id);
  }));
}

// ------------------------------------------------------------------
// 작업 상세 + 검수 + 조립
// ------------------------------------------------------------------
async function renderJobDetail(jobId) {
  const root = document.getElementById("tab-job-detail");
  root.classList.remove("hidden");
  root.innerHTML = `<div class="card"><p>불러오는 중...</p></div>`;

  let job;
  try { job = await api(`/api/jobs/${jobId}`); } catch (e) {
    root.innerHTML = `<div class="card error-box">${e.message}</div>`;
    return;
  }

  const stageLabel = { tts: "목소리", align: "자막 정렬", plan: "씬 분할", review: "검수 승인", assemble: "초안 조립" };
  const stepsHtml = Object.entries(job.status).map(([k, v]) =>
    `<li class="${v === "done" ? "done" : v === "error" ? "error" : ""}">${stageLabel[k] || k}: ${v === "done" ? "완료" : v === "error" ? "오류" : "대기"}</li>`
  ).join("");

  let warnBox = job.warnings.length ? `<div class="warn-box">${job.warnings.join("\n")}</div>` : "";

  root.innerHTML = `
    <div class="card">
      <h3>작업: ${jobId}</h3>
      <ul class="step-list">${stepsHtml}</ul>
      ${warnBox}
      <div class="row" style="margin-top:10px;">
        <button class="secondary" id="btn-review">검수 화면 열기</button>
        <button class="secondary" id="btn-rework-tts">목소리만 다시</button>
        <button class="secondary" id="btn-rework-align">자막 정렬만 다시</button>
        <button class="secondary" id="btn-rework-plan">씬 분할만 다시</button>
      </div>
      <div id="rework-status"></div>
    </div>
    <div id="review-area"></div>
  `;

  document.getElementById("btn-review").addEventListener("click", () => renderReview(jobId));
  document.getElementById("btn-rework-tts").addEventListener("click", () => rework(jobId, "tts"));
  document.getElementById("btn-rework-align").addEventListener("click", () => rework(jobId, "align"));
  document.getElementById("btn-rework-plan").addEventListener("click", () => rework(jobId, "plan"));

  if (job.status.plan === "done") renderReview(jobId);
}

async function rework(jobId, stage) {
  const status = document.getElementById("rework-status");
  status.textContent = "다시 만드는 중...";
  try {
    await api(`/api/jobs/${jobId}/rework/${stage}`, { method: "POST" });
    status.innerHTML = `<div class="status-line status-ok">${stage} 다시 완료</div>`;
    renderJobDetail(jobId);
  } catch (e) {
    status.innerHTML = `<div class="error-box">${e.message}</div>`;
  }
}

async function renderReview(jobId) {
  const area = document.getElementById("review-area");
  area.innerHTML = `<div class="card"><p>불러오는 중...</p></div>`;
  let data;
  try { data = await api(`/api/jobs/${jobId}/review`); } catch (e) {
    area.innerHTML = `<div class="card error-box">${e.message}</div>`;
    return;
  }

  const cost = data.missing_cost;
  const cards = data.scenes.map(sc => sceneCardHtml(sc)).join("");

  area.innerHTML = `
    <div class="card">
      <h3>검수 — 씬마다 확인하고 승인하기 전까지는 영상 변환이 돌지 않습니다</h3>
      <div class="row">
        <button class="secondary" id="btn-fill-missing" ${cost.count === 0 ? "disabled" : ""}>
          빈 씬 ${cost.count}개 채우기 (<span class="cost-tag">약 ${cost.won.toLocaleString()}원</span>)
        </button>
        <button class="secondary" id="btn-center-all">전체 정중앙</button>
        <button class="primary" id="btn-approve" ${data.approved ? "disabled" : ""}>
          ${data.approved ? "승인됨 — 아래에서 초안 만들기" : "검수 승인"}
        </button>
      </div>
      <div id="review-status"></div>
    </div>
    <div class="scene-grid">${cards}</div>
    <div class="card">
      <button class="primary" id="btn-assemble" ${data.approved ? "" : "disabled"}>캡컷 초안 만들기</button>
      <span class="scene-note" style="height:auto;">${data.approved ? "" : "먼저 위에서 검수 승인을 눌러주세요."}</span>
      <div id="assemble-status"></div>
    </div>
  `;

  document.getElementById("btn-fill-missing").addEventListener("click", async () => {
    document.getElementById("review-status").textContent = "만드는 중...";
    try { await api(`/api/jobs/${jobId}/scenes/fill-missing`, { method: "POST" }); renderReview(jobId); }
    catch (e) { document.getElementById("review-status").innerHTML = `<div class="error-box">${e.message}</div>`; }
  });

  document.getElementById("btn-center-all").addEventListener("click", async () => {
    await api(`/api/jobs/${jobId}/center-all`, { method: "POST" });
    renderReview(jobId);
  });

  document.getElementById("btn-approve").addEventListener("click", async () => {
    await api(`/api/jobs/${jobId}/approve`, { method: "POST" });
    renderReview(jobId);
  });

  document.getElementById("btn-assemble").addEventListener("click", async () => {
    const status = document.getElementById("assemble-status");
    status.textContent = "캡컷 초안을 만드는 중입니다...";
    try {
      const result = await api(`/api/jobs/${jobId}/assemble`, { method: "POST" });
      status.innerHTML = renderCompletion(result);
    } catch (e) {
      status.innerHTML = `<div class="error-box">${e.message}</div>`;
    }
  });

  wireSceneCards(jobId, data.scenes);
}

function sceneCardHtml(sc) {
  return `
    <div class="scene-card" data-scene-id="${sc.id}">
      <div class="img-slot"></div>
      <div class="scene-note">${sc.note || sc.image_prompt || ""}</div>
      <div class="row">
        <button class="secondary btn-remake">다시 만들기 (<span class="cost-tag">약 55원</span>)</button>
      </div>
      <div class="row" style="margin-top:6px;">
        <select class="mode-select">
          <option value="fill">꽉채움</option>
          <option value="full">전체</option>
          <option value="zoom">확대</option>
        </select>
        <input type="file" class="file-input" accept="video/*,image/*" />
      </div>
      <div class="row" style="margin-top:6px;">
        <span class="pct-badge">화면 점유 -</span>
        <label style="font-size:11px;"><input type="checkbox" class="fine-toggle" /> 미세</label>
      </div>
      <div class="nudge-grid">
        <span></span><button class="nudge-btn" data-axis="y" data-dir="-1">▲</button><span></span>
        <button class="nudge-btn" data-axis="x" data-dir="-1">◀</button>
        <button class="btn-center" title="정중앙">⊙</button>
        <button class="nudge-btn" data-axis="x" data-dir="1">▶</button>
        <span></span><button class="nudge-btn" data-axis="y" data-dir="1">▼</button><span></span>
      </div>
    </div>`;
}

function wireSceneCards(jobId, scenes) {
  document.querySelectorAll(".scene-card").forEach(card => {
    const sceneId = card.dataset.sceneId;
    const scene = scenes.find(s => String(s.id) === String(sceneId));
    const imgSlot = card.querySelector(".img-slot");

    function paintImage() {
      if (scene.rel_path) {
        const t = Date.now();
        const isVideo = /\.(mp4|mov|m4v|webm)$/i.test(scene.rel_path);
        const src = `/media/${jobId}/${scene.rel_path}?t=${t}`;
        imgSlot.innerHTML = isVideo
          ? `<video src="${src}" muted loop autoplay playsinline></video>`
          : `<img src="${src}" />`;
      } else {
        imgSlot.innerHTML = `<div class="empty-box">아직 재료 없음<br>${scene.note || ""}</div>`;
      }
    }
    paintImage();
    if (scene.mode) card.querySelector(".mode-select").value = scene.mode;

    card.querySelector(".btn-remake").addEventListener("click", async () => {
      try {
        await api(`/api/jobs/${jobId}/scenes/${sceneId}/image?force=true`, { method: "POST" });
        renderReview(jobId);
      } catch (e) { alert(e.message); }
    });

    card.querySelector(".btn-center").addEventListener("click", async () => {
      const pct = await api(`/api/jobs/${jobId}/scenes/${sceneId}/center`, { method: "POST" });
      card.querySelector(".pct-badge").textContent = "화면 점유 계산됨";
    });

    card.querySelectorAll(".nudge-btn").forEach(btn => {
      btn.addEventListener("click", async () => {
        const fine = card.querySelector(".fine-toggle").checked;
        try {
          const result = await api(`/api/jobs/${jobId}/scenes/${sceneId}/nudge`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ axis: btn.dataset.axis, direction: parseInt(btn.dataset.dir, 10), fine }),
          });
          card.querySelector(".pct-badge").textContent = `화면 점유 ${result.occupied_pct}%`;
        } catch (e) { alert(e.message); }
      });
    });

    card.querySelector(".file-input").addEventListener("change", async (ev) => {
      const file = ev.target.files[0];
      if (!file) return;
      const mode = card.querySelector(".mode-select").value;
      const form = new FormData();
      form.append("file", file);
      form.append("mode", mode);
      form.append("aspect", "0.5625");
      form.append("pos_x", "0.5");
      form.append("pos_y", "0.5");
      try {
        await api(`/api/jobs/${jobId}/scenes/${sceneId}/media`, { method: "POST", body: form });
        renderReview(jobId);
      } catch (e) { alert(e.message); }
    });

    card.querySelector(".mode-select").addEventListener("change", async (ev) => {
      const form = new FormData();
      form.append("mode", ev.target.value);
      form.append("aspect", "0.5625");
      form.append("pos_x", "0.5");
      form.append("pos_y", "0.5");
      try { await api(`/api/jobs/${jobId}/scenes/${sceneId}/media`, { method: "POST", body: form }); }
      catch (e) { alert(e.message); }
    });
  });
}

function renderCompletion(result) {
  return `
    <div class="status-line status-ok">완료! 초안 위치: ${result.draft_path}</div>
    <div class="warn-box">
      캡컷을 열기 전에 이 세 가지를 챙기세요:<br>
      ① 유튜브 스튜디오 → 관련 동영상에 원본 롱폼 연결하기<br>
      ② 마지막 5초에 "말 + 화면"으로 안내 넣기<br>
      ③ 연결한 롱폼은 첫 5~10초 안에 약속한 걸 보여주기
    </div>`;
}

// 시작 화면
renderSetup();
