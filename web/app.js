/* 轻刮 QingGua — local, dependency-free UI */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const STATUS = {
    new: { label: "未匹配", cls: "" },
    review: { label: "待确认", cls: "warn" },
    matched: { label: "已匹配", cls: "info" },
    applied: { label: "已刮削", cls: "ok" },
    error: { label: "失败", cls: "bad" },
  };
  const KIND = { movie: "电影", tv: "剧集" };

  const state = {
    version: "0.1",
    settings: {},
    stats: {},
    folders: [],
    series: [],
    seriesKey: null,
    seriesName: "",
    works: [],
    filter: "all",
    status: "all",
    folder: null,
    q: "",
    detail: null,
    candidates: [],
    selected: null,
    searchInfo: null,
    tab: "match",
    searching: false,
    loadSeq: 0,
    job: null,
    jobTimer: null,
    confirmAction: null,
  };

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));

  const icon = (name, cls = "") =>
    `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"></use></svg>`;

  async function api(path, options = {}) {
    const init = { method: options.method || "GET", headers: {} };
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    const response = await fetch(path, init);
    let data = null;
    try { data = await response.json(); } catch (_) { data = null; }
    if (!response.ok || (data && data.ok === false)) {
      throw new Error((data && data.error) || `请求失败（${response.status}）`);
    }
    return data || {};
  }

  function toast(message, type = "ok", title = "") {
    const root = $("#toast-root");
    const node = document.createElement("div");
    node.className = `toast ${type}`;
    node.innerHTML = `${title ? `<strong>${esc(title)}</strong>` : ""}${esc(message)}`;
    root.appendChild(node);
    setTimeout(() => {
      node.style.opacity = "0";
      node.style.transform = "translateY(-4px)";
      node.style.transition = "all .2s";
      setTimeout(() => node.remove(), 220);
    }, 3800);
  }

  function hueFor(text) {
    let value = 0;
    for (const ch of String(text || "?")) value = (value * 31 + ch.codePointAt(0)) % 360;
    return value;
  }

  function titleOf(item) {
    return item.title || item.parsed_title || "未命名";
  }

  function posterMarkup(item, className, options = {}) {
    const title = titleOf(item);
    const hue = hueFor(title);
    const img = item.poster
      ? `<img src="/api/works/${item.id}/poster?v=${encodeURIComponent(item.updated_at || "")}" alt="" loading="lazy" onerror="this.remove()">`
      : "";
    const kindText = [KIND[item.kind] || "", item.year || ""].filter(Boolean).join(" · ");
    return `<div class="${className}">
      <div class="poster-placeholder" style="--h:${hue}">
        <span class="poster-letter">${esc(title.slice(0, 1))}</span>
        <span class="poster-name">${esc(title)}</span>
        <span class="poster-kind">${esc(kindText || "未知类型")}</span>
      </div>${img}
    </div>`;
  }

  function fmtSize(bytes) {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = Number(bytes);
    let index = 0;
    while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
    return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
  }

  function providerName(item) {
    if (item.provider_label) return item.provider_label;
    return {
      douban: "豆瓣", fankuhub: "番库", bilibili: "哔哩哔哩",
      tmdb: "TMDb", demo: "演示", manual: "手动",
    }[item.provider] || "未匹配";
  }

  function stat(id, value) {
    const node = $(`#${id}`);
    if (node) node.textContent = String(value ?? 0);
  }

  async function bootstrap() {
    const data = await api("/api/bootstrap");
    state.version = data.version;
    state.settings = data.settings || {};
    state.stats = data.stats || {};
    state.folders = data.folders || [];
    state.series = data.series || [];
    if (state.seriesKey && !state.series.some((item) => item.key === state.seriesKey)) {
      state.seriesKey = null;
      state.seriesName = "";
    }
    $("#version").textContent = `v${state.version}`;
    renderChrome();
  }

  function renderChrome() {
    const stats = state.stats || {};
    stat("count-all", stats.works || 0);
    stat("count-movie", stats.movies || 0);
    stat("count-tv", stats.shows || 0);
    stat("count-attention", (stats.review || 0) + (stats.errors || 0));
    stat("count-applied", stats.applied || 0);
    stat("stat-works", stats.works || 0);
    stat("stat-files", `${stats.episodes || 0} 个视频文件`);
    stat("stat-applied", stats.applied || 0);
    stat("stat-review", (stats.review || 0) + (stats.errors || 0));
    stat("stat-episodes", stats.episodes || 0);

    $("#page-title").textContent = state.seriesKey
      ? `系列 · ${state.seriesName}`
      : {
          all: "媒体库", movie: "电影", tv: "剧集",
          attention: "待确认", applied: "已刮削",
        }[state.filter] || "媒体库";

    const hasWorks = (stats.works || 0) > 0;
    $("#hero").hidden = hasWorks || Boolean(state.q);

    const source = state.settings.source || "auto";
    const tmdbOn = Boolean(state.settings.tmdb_enabled && state.settings.tmdb_key_set);
    const sourceNames = { douban: "豆瓣", fankuhub: "番库", bilibili: "哔哩哔哩", tmdb: "TMDb" };
    const sourceDesc = {
      douban: "无需 API Key",
      fankuhub: "动漫条目 / 无需 Key",
      bilibili: "番剧条目 / 无需 Key",
      tmdb: state.settings.tmdb_key_set ? "使用你的 API Key" : "尚未配置 API Key",
    };
    if (source === "auto") {
      const chain = [];
      if (state.settings.douban_enabled) chain.push("豆瓣");
      if (state.settings.fankuhub_enabled) chain.push("番库");
      if (state.settings.bilibili_enabled) chain.push("B站");
      if (tmdbOn) chain.push("TMDb");
      $("#source-name").textContent = chain[0] || "未启用";
      $("#source-desc").textContent = chain.length ? `${chain.join(" → ")} 兜底` : "请到设置中开启来源";
    } else {
      $("#source-name").textContent = sourceNames[source] || "未知来源";
      $("#source-desc").textContent = sourceDesc[source] || "无需 API Key";
    }
    $("#source-dot").className = `source-dot ${source === "tmdb" && !state.settings.tmdb_key_set ? "off" : ""}`;

    const list = $("#folder-list");
    if (!state.folders.length) {
      list.innerHTML = `<div class="rail-empty">还没有添加目录</div>`;
    } else {
      list.innerHTML = state.folders.map((folder) => {
        const name = folder.path.split(/[\\/]/).filter(Boolean).pop() || folder.path;
        return `<button class="folder ${state.folder === folder.id ? "active" : ""}" data-action="folder" data-id="${folder.id}" title="${esc(folder.path)}">
          ${icon("folder")}<span>${esc(name)}</span><b>${folder.work_count || 0}</b>
          <span class="remove" data-action="remove-folder" data-id="${folder.id}" title="移除目录">${icon("close")}</span>
        </button>`;
      }).join("");
    }

    const seriesSection = $("#series-section");
    const seriesList = $("#series-list");
    if (seriesSection && seriesList) {
      seriesSection.hidden = !state.series.length;
      seriesList.innerHTML = state.series
        .map((item) => `<button class="series-item ${state.seriesKey === item.key ? "active" : ""}" data-action="series" data-key="${esc(item.key)}" title="${esc(item.name)} 系列">
          <span>${esc(item.name)}</span><b>${item.count}</b>
        </button>`)
        .join("");
    }
    renderToolbarHint();
  }

  function renderToolbarHint() {
    const hint = $("#toolbar-hint");
    if (!hint) return;
    if (state.seriesKey) {
      hint.textContent = "同一系列下的作品，点左侧“全部作品”可退出";
      return;
    }
    hint.textContent = {
      attention: "低置信度或失败项，确认后再写入",
      applied: "NFO 与海报已写入媒体目录",
      movie: "电影按文件识别",
      tv: "剧集按季集自动分组",
    }[state.filter] || "";
  }

  async function loadWorks() {
    const seq = ++state.loadSeq;
    const params = new URLSearchParams();
    if (state.folder) params.set("folder_id", state.folder);
    if (state.seriesKey) params.set("series", state.seriesKey);
    if (state.status !== "all") params.set("status", state.status);
    else if (state.filter === "attention") params.set("status", "attention");
    else if (state.filter === "applied") params.set("status", "applied");
    if (state.filter === "movie" || state.filter === "tv") params.set("kind", state.filter);
    if (state.q) params.set("q", state.q);
    try {
      const data = await api(`/api/works?${params.toString()}`);
      if (seq !== state.loadSeq) return;
      state.works = data.items || [];
      state.stats = data.stats || state.stats;
      state.folders = data.folders || state.folders;
      renderChrome();
      renderWorks();
    } catch (error) {
      if (seq !== state.loadSeq) return;
      toast(error.message, "bad", "读取媒体库失败");
      state.works = [];
      renderWorks();
    }
  }

  function renderWorks() {
    const works = state.works;
    const grid = $("#works");
    const empty = $("#empty");
    $("#result-count").textContent = `${works.length} 个作品`;
    grid.innerHTML = works.map(workCard).join("");

    if (!works.length) {
      empty.hidden = false;
      const hasFolders = state.folders.length > 0;
      if (state.q) {
        $("#empty-title").textContent = "没有找到匹配的作品";
        $("#empty-text").textContent = `没有标题或目录包含“${state.q}”。换个关键词，或者清空搜索。`;
        $(".empty-actions").innerHTML = `<button class="btn ghost" data-action="clear-search">${icon("close")}清空搜索</button>`;
      } else if (!hasFolders) {
        $("#empty-title").textContent = "还没有作品";
        $("#empty-text").textContent = "添加一个存放电影或剧集的文件夹，轻刮会自己识别标题、年份和季集。";
        $(".empty-actions").innerHTML = `<button class="btn primary" data-action="add-folder">${icon("plus")}添加文件夹</button><button class="btn ghost" data-action="demo">载入演示库</button>`;
      } else if (state.seriesKey) {
        $("#empty-title").textContent = "这个系列下没有作品";
        $("#empty-text").textContent = "点左侧“全部作品”退出系列筛选。";
        $(".empty-actions").innerHTML = `<button class="btn ghost" data-action="reset-filter">${icon("refresh")}查看全部</button>`;
      } else if (state.filter !== "all" || state.status !== "all" || state.folder) {
        $("#empty-title").textContent = "这个筛选下没有作品";
        $("#empty-text").textContent = "换个筛选条件，或者回到全部作品看看。";
        $(".empty-actions").innerHTML = `<button class="btn ghost" data-action="reset-filter">${icon("refresh")}查看全部</button>`;
      } else {
        $("#empty-title").textContent = "目录还是空的";
        $("#empty-text").textContent = "点“扫描”重新读取，或者确认目录里确实有 mkv / mp4 等视频文件。";
        $(".empty-actions").innerHTML = `<button class="btn primary" data-action="scan">${icon("scan")}扫描目录</button>`;
      }
    } else {
      empty.hidden = true;
    }
  }

  function workCard(item) {
    const status = STATUS[item.status] || STATUS.new;
    const title = titleOf(item);
    const episodes = item.kind === "tv" ? `${item.episode_count || item.file_count || 0} 集` : "电影";
    const seasonText = item.kind === "tv" && Number(item.season_count || 1) > 1
      ? `${item.season_count} 季 · ${episodes}`
      : episodes;
    const rating = item.rating ? `<span class="score">★ ${Number(item.rating).toFixed(1)}</span>` : "";
    return `<article class="work-card" data-action="open-work" data-id="${item.id}" tabindex="0" role="button" aria-label="${esc(title)}">
      ${posterMarkup(item, "work-poster")}
      <div class="work-badges">
        <span class="badge ${status.cls}">${status.label}</span>
        ${item.provider && item.status !== "new" ? `<span class="badge provider">${esc(providerName(item))}</span>` : ""}
      </div>
      <div class="work-info">
        <div class="work-title">${esc(title)}</div>
        <div class="work-meta"><span>${esc(item.year || "年份未知")}</span><span class="dot"></span><span>${seasonText}</span></div>
        <div class="work-foot"><span>${esc(providerName(item))}</span>${rating || `<span>${item.confidence ? `匹配度 ${item.confidence}` : ""}</span>`}</div>
      </div>
    </article>`;
  }

  async function openWork(id) {
    try {
      const data = await api(`/api/works/${id}`);
      state.detail = data.work;
      state.candidates = [];
      state.selected = null;
      state.searchInfo = null;
      const multiSeason = (state.detail.seasons || []).length > 1;
      state.tab = ["matched", "applied"].includes(state.detail.status) ? "info" : "match";
      state.searching = false;
      renderDrawer();
      $("#drawer").classList.add("open");
      $("#drawer").setAttribute("aria-hidden", "false");
      $("#drawer-scrim").hidden = false;
      if (multiSeason && ["new", "review"].includes(state.detail.status)) {
        toast("检测到多季，已按第一季集数粗筛；可切换季再搜", "warn", "多季作品");
        setTimeout(() => searchWork(true), 300);
      } else if (["new", "review"].includes(state.detail.status) && (state.detail.parsed_title || state.detail.title)) {
        setTimeout(() => searchWork(true), 260);
      }
    } catch (error) {
      toast(error.message, "bad", "打开失败");
    }
  }

  function closeDrawer() {
    $("#drawer").classList.remove("open");
    $("#drawer").setAttribute("aria-hidden", "true");
    $("#drawer-scrim").hidden = true;
  }

  function renderDrawer() {
    const work = state.detail;
    if (!work) return;
    const meta = work.meta || {};
    const status = STATUS[work.status] || STATUS.new;
    const title = titleOf(work);
    const rating = meta.rating ? `<span class="meta-pill rating">★ ${Number(meta.rating).toFixed(1)}</span>` : "";
    const path = work.kind === "movie" && work.files?.length ? work.files[0].path : work.root_path;
    const hasMeta = Boolean(work.meta && (work.meta.title || work.meta.overview));

    const tabs = [
      ["match", "匹配"],
      ["info", "信息"],
      ["files", `文件 ${work.files?.length || 0}`],
    ].map(([id, label]) => `<button class="tab ${state.tab === id ? "active" : ""}" data-action="tab" data-tab="${id}">${label}</button>`).join("");

    let body = "";
    if (state.tab === "match") body = renderMatchTab(work, meta);
    else if (state.tab === "info") body = renderInfoTab(work, meta);
    else body = renderFilesTab(work);

    $("#drawer-body").innerHTML = `
      <div class="detail-top">
        ${posterMarkup(work, "detail-poster")}
        <div class="detail-info">
          <h2>${esc(title)}</h2>
          ${meta.original_title && meta.original_title !== title ? `<div class="detail-alt">${esc(meta.original_title)}</div>` : ""}
          <div class="detail-meta">
            <span class="meta-pill">${KIND[work.kind] || ""}</span>
            ${(work.seasons || []).length > 1 ? `<span class="meta-pill">${work.seasons.length} 季</span>` : work.season > 1 ? `<span class="meta-pill">第 ${work.season} 季</span>` : ""}
            <span class="meta-pill">${esc(work.year || "年份未知")}</span>
            <span class="meta-pill ${status.cls}">${status.label}</span>
            ${meta.episodes_count ? `<span class="meta-pill">${meta.episodes_count} 集</span>` : ""}
            ${rating}
            ${work.provider ? `<span class="meta-pill">${esc(providerName(work))}</span>` : ""}
          </div>
          <div class="detail-path" title="${esc(path)}">${esc(path)}</div>
          <div class="detail-actions">
            <button class="btn primary small" data-action="apply-existing" ${hasMeta ? "" : "disabled"}>${icon("check")}应用并写 NFO</button>
            <button class="btn ghost small" data-action="save-manual">${icon("edit")}保存修改</button>
            <button class="btn ghost small" data-action="open-folder">${icon("external")}打开目录</button>
            <button class="btn danger small" data-action="delete-work">${icon("trash")}删除</button>
          </div>
        </div>
      </div>
      <div class="tabs">${tabs}</div>
      <div class="tab-body">${body}</div>
    `;
    renderCandidates();
  }

  function renderMatchTab(work, meta) {
    const provider = state.searchInfo?.provider || (state.settings.source || "auto");
    const query = state.searchInfo?.query ?? (work.parsed_title || work.title || "");
    const year = state.searchInfo?.year ?? (work.parsed_year || work.year || "");
    const seasons = (work.seasons && work.seasons.length) ? work.seasons : [work.season || 1];
    const seasonCounts = work.season_counts || {};
    const currentSeason = state.searchInfo?.season ?? seasons[0];
    const currentCount = Number(seasonCounts[currentSeason] || work.episode_count || 0);
    const seasonControl = seasons.length > 1
      ? `<div class="season-row">
          <label class="label" for="search-season">按季匹配</label>
          <select class="select compact" id="search-season">
            ${seasons.map((value) => `<option value="${value}" ${Number(value) === Number(currentSeason) ? "selected" : ""}>第 ${value} 季${seasonCounts[value] ? `（${seasonCounts[value]} 集）` : ""}</option>`).join("")}
          </select>
          <span class="hint">该作品包含 ${seasons.length} 季，自动刮削会跳过；请逐季搜索并确认。</span>
        </div>`
      : `<div class="hint">文件识别为第 ${seasons[0]} 季${currentCount ? `，共 ${currentCount} 集` : ""}。</div>`;
    return `
      <div class="search-row">
        <input class="input" id="search-query" value="${esc(query)}" placeholder="片名 / 剧名">
        <input class="input" id="search-year" value="${esc(year)}" placeholder="年份" inputmode="numeric">
        <select class="select" id="search-provider">
          <option value="auto" ${provider === "auto" ? "selected" : ""}>自动</option>
          <option value="douban" ${provider === "douban" ? "selected" : ""}>豆瓣</option>
          <option value="fankuhub" ${provider === "fankuhub" ? "selected" : ""}>番库（动漫）</option>
          <option value="bilibili" ${provider === "bilibili" ? "selected" : ""}>哔哩哔哩</option>
          <option value="tmdb" ${provider === "tmdb" ? "selected" : ""}>TMDb</option>
        </select>
        <button class="btn" data-action="search">${icon("search")}搜索</button>
      </div>
      ${seasonControl}
      <div id="search-errors">${(state.searchInfo?.errors || []).map((e) => `<div class="notice bad">${esc(e)}</div>`).join("")}</div>
      ${state.searchInfo?.episode_count ? `<div class="hint">已按本季 ${state.searchInfo.episode_count} 集粗筛候选；集数对不上的结果会被压到后面。</div>` : ""}
      <div class="hint">${meta?.overview ? "已有一份匹配结果。重新搜索会覆盖当前选择；写入前可以先预览。" : "按文件名自动识别标题和年份。搜不到时，手动输入中文名再试一次。"}</div>
      <div class="candidates" id="candidate-list"></div>
      <div class="form-actions">
        <button class="btn primary" data-action="apply-selected" ${state.selected ? "" : "disabled"}>${icon("check")}应用选中结果</button>
      </div>
    `;
  }

  function renderInfoTab(work, meta) {
    const genres = Array.isArray(meta.genres) ? meta.genres.join(" / ") : meta.genres || "";
    const countries = Array.isArray(meta.countries) ? meta.countries.join(" / ") : meta.countries || "";
    return `
      <div class="form-grid">
        <div class="form-row"><label class="label">标题（写入 NFO）</label><input class="input" id="edit-title" value="${esc(meta.title || work.title || "")}"></div>
        <div class="form-row"><label class="label">年份</label><input class="input" id="edit-year" value="${esc(meta.year || work.year || "")}" inputmode="numeric"></div>
      </div>
      <div class="form-row"><label class="label">原名</label><input class="input" id="edit-original" value="${esc(meta.original_title || "")}"></div>
      <div class="setting wide">
        <div class="setting-head"><div><div class="setting-name">NFO 封面</div><div class="setting-desc">支持图片 URL 或本地图片路径；写入时复制到媒体目录并更新 NFO 的 thumb。</div></div></div>
        <div class="cover-edit">
          <input class="input" id="edit-poster" value="${esc(meta.poster_url || "")}" placeholder="https://… 或 D:\\图片\\poster.jpg">
          <button class="btn small" data-action="browse-poster">${icon("image")}浏览…</button>
        </div>
      </div>
      <div class="form-grid three">
        <div class="form-row"><label class="label">评分</label><input class="input" id="edit-rating" value="${esc(meta.rating || "")}" inputmode="decimal"></div>
        <div class="form-row"><label class="label">片长（分钟）</label><input class="input" id="edit-runtime" value="${esc(meta.runtime || "")}" inputmode="numeric"></div>
        <div class="form-row"><label class="label">上映日期</label><input class="input" id="edit-date" value="${esc(meta.release_date || "")}" placeholder="YYYY-MM-DD"></div>
      </div>
      <div class="form-row"><label class="label">类型</label><input class="input" id="edit-genres" value="${esc(genres)}" placeholder="科幻 / 冒险"></div>
      <div class="form-row"><label class="label">国家 / 地区</label><input class="input" id="edit-countries" value="${esc(countries)}" placeholder="中国大陆 / 美国"></div>
      <div class="form-row"><label class="label">简介</label><textarea class="textarea" id="edit-overview" placeholder="剧情简介">${esc(meta.overview || "")}</textarea></div>
      <div class="hint">保存只更新本地元数据。点上方“应用并写 NFO”才会把海报、简介和 NFO 写进媒体目录。</div>
      <div class="form-actions">
        <button class="btn" data-action="save-manual">${icon("edit")}保存这些修改</button>
      </div>
    `;
  }

  function renderFilesTab(work) {
    const files = work.files || [];
    return `
      <div class="file-list">
        ${files.length ? files.map((file) => {
          const ep = work.kind === "tv" ? `S${String(file.season || 1).padStart(2, "0")}E${String(file.episode || 0).padStart(2, "0")}` : "正片";
          return `<div class="file-row">
            <span class="file-ep">${ep}</span>
            <span class="file-name" title="${esc(file.path)}">${esc(file.path.split(/[\\/]/).pop())}</span>
            <span class="file-size">${fmtSize(file.size)}</span>
            ${file.nfo_path ? `<span class="file-nfo">NFO</span>` : ""}
          </div>`;
        }).join("") : `<div class="empty-candidates">没有关联文件</div>`}
      </div>
      <div class="nfo-actions">
        <button class="btn small" data-action="nfo">${icon("image")}预览 NFO</button>
        <button class="btn small" data-action="organize">${icon("folder")}整理预览</button>
      </div>
      <div class="hint">整理会把文件移动为 <code>片名 (年份)/Season 01/片名 - S01E02.ext</code>，执行前会先给你看完整移动清单。</div>
    `;
  }

  function renderCandidates() {
    const list = $("#candidate-list");
    if (!list) return;
    if (state.searching) {
      list.innerHTML = `<div class="empty-candidates">正在搜索…</div>`;
      return;
    }
    if (!state.candidates.length) {
      list.innerHTML = state.searchInfo ? `<div class="empty-candidates">没有找到候选。试试只保留片名，或去掉季集信息。</div>` : `<div class="empty-candidates">点“搜索”开始匹配。也可以直接修改“信息”里的字段。</div>`;
      return;
    }
    list.innerHTML = state.candidates.map((candidate, index) => {
      const selected = state.selected && state.selected.provider === candidate.provider && state.selected.id === candidate.id;
      const poster = candidate.poster_url
        ? `<div class="candidate-poster"><img src="${esc(candidate.poster_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()"></div>`
        : `<div class="candidate-poster"><div class="poster-placeholder" style="--h:${hueFor(candidate.title)}"><span class="poster-name" style="font-size:8px">${esc(candidate.title)}</span></div></div>`;
      return `<button class="candidate ${selected ? "selected" : ""}" data-action="select-candidate" data-index="${index}">
        ${poster}
        <div class="candidate-main">
          <div class="candidate-title">${esc(candidate.title)}</div>
          <div class="candidate-sub">
            <span>${esc(candidate.year || "年份未知")}</span>
            <span>${KIND[candidate.kind] || ""}</span>
            ${candidate.episode_count ? `<span>${candidate.episode_count} 集</span>` : ""}
            ${candidate.rating ? `<span>★ ${Number(candidate.rating).toFixed(1)}</span>` : ""}
            <span>${esc(candidate.source_label || candidate.provider)}</span>
          </div>
        </div>
        <div class="candidate-score">${candidate.score ?? "-"}<small>匹配度</small></div>
      </button>`;
    }).join("");
    const apply = $('[data-action="apply-selected"]');
    if (apply) apply.disabled = !state.selected;
  }

  function selectCandidate(index) {
    state.selected = state.candidates[index] || null;
    $$(".candidate").forEach((node, i) => node.classList.toggle("selected", i === index));
    const apply = $('[data-action="apply-selected"]');
    if (apply) apply.disabled = !state.selected;
  }

  function setButtonLoading(button, loading, label = "处理中…") {
    if (!button) return;
    if (loading) {
      button.dataset.original = button.innerHTML;
      button.disabled = true;
      button.innerHTML = `<span class="job-spinner" style="width:13px;height:13px;border-width:1.5px"></span>${esc(label)}`;
    } else if (button.dataset.original) {
      button.innerHTML = button.dataset.original;
      button.disabled = false;
      delete button.dataset.original;
    }
  }

  async function searchWork(auto = false) {
    if (!state.detail) return;
    const button = $('[data-action="search"]');
    if (!auto) setButtonLoading(button, true, "搜索中…");
    state.searching = true;
    state.candidates = [];
    state.selected = null;
    renderCandidates();
    const query = $("#search-query") ? $("#search-query").value.trim() : (state.detail.parsed_title || state.detail.title || "");
    const yearText = $("#search-year") ? $("#search-year").value.trim() : "";
    const provider = $("#search-provider") ? $("#search-provider").value : "auto";
    const season = $("#search-season") ? Number($("#search-season").value) : null;
    try {
      const data = await api(`/api/works/${state.detail.id}/search`, {
        method: "POST",
        body: {
          query,
          year: yearText || null,
          provider,
          season,
        },
      });
      state.searchInfo = {
        query: data.query,
        year: data.year,
        season: data.season,
        episode_count: data.episode_count,
        errors: data.errors || [],
        provider,
      };
      state.candidates = data.candidates || [];
      if (state.candidates.length && Number(state.candidates[0].score || 0) >= 80) {
        state.selected = state.candidates[0];
      }
      state.searching = false;
      renderDrawer();
      if (!state.candidates.length) {
        toast(data.errors?.join("；") || "没有找到候选，试试手动输入片名", "warn", "没有匹配");
      }
    } catch (error) {
      state.searching = false;
      state.searchInfo = { query, year: yearText, season, errors: [error.message], provider };
      renderDrawer();
      toast(error.message, "bad", "搜索失败");
    } finally {
      if (!auto) setButtonLoading(button, false);
    }
  }

  function applySelected() {
    if (!state.selected || !state.detail) return;
    const seasons = state.detail.seasons || [];
    if (seasons.length > 1) {
      const season = state.searchInfo?.season || seasons[0];
      confirmDanger(
        "多季作品，请确认",
        `该作品包含 ${seasons.length} 季。当前只会按第 ${season} 季匹配并写入 NFO，集数季信息仍按文件中的 SxxExx 保留。确认继续？`,
        () => applySelectedConfirmed(),
        "仍要写入",
      );
      return;
    }
    applySelectedConfirmed();
  }

  async function applySelectedConfirmed() {
    if (!state.selected || !state.detail) return;
    const candidate = state.selected;
    const button = $('[data-action="apply-selected"]');
    setButtonLoading(button, true, "写入中…");
    try {
      const data = await api(`/api/works/${state.detail.id}/apply`, {
        method: "POST",
        body: { provider: candidate.provider, id: candidate.id },
      });
      await afterApply(data);
    } catch (error) {
      toast(error.message, "bad", "写入失败");
    } finally {
      setButtonLoading(button, false);
    }
  }

  function applyExisting() {
    if (!state.detail) return;
    const seasons = state.detail.seasons || [];
    if (seasons.length > 1) {
      confirmDanger(
        "多季作品，请确认",
        `该作品包含 ${seasons.length} 季。当前元数据只会写入一份 tvshow.nfo，单集 NFO 的季信息仍来自文件名。确认继续？`,
        () => applyExistingConfirmed(),
        "仍要写入",
      );
      return;
    }
    applyExistingConfirmed();
  }

  async function applyExistingConfirmed() {
    if (!state.detail) return;
    const button = $('[data-action="apply-existing"]');
    setButtonLoading(button, true, "写入中…");
    try {
      const data = await api(`/api/works/${state.detail.id}/apply`, { method: "POST", body: {} });
      await afterApply(data);
    } catch (error) {
      toast(error.message, "bad", "写入失败");
    } finally {
      setButtonLoading(button, false);
    }
  }

  async function afterApply(data) {
    const warnings = data.warnings || [];
    toast(
      warnings.length ? `已完成，但有 ${warnings.length} 个提示` : "NFO 与海报已写入",
      warnings.length ? "warn" : "ok",
      titleOf(data.work || {}),
    );
    state.detail = data.work;
    state.tab = "info";
    state.selected = null;
    state.candidates = [];
    state.searchInfo = null;
    renderDrawer();
    await loadWorks();
  }

  async function saveManual() {
    if (!state.detail) return;
    const button = $('[data-action="save-manual"]');
    const body = {
      title: $("#edit-title")?.value.trim(),
      year: $("#edit-year")?.value.trim() || null,
      original_title: $("#edit-original")?.value.trim(),
      rating: $("#edit-rating")?.value.trim() || null,
      runtime: $("#edit-runtime")?.value.trim() || null,
      release_date: $("#edit-date")?.value.trim() || null,
      genres: ($("#edit-genres")?.value || "").split(/\s*[\/,，]\s*/).filter(Boolean),
      countries: ($("#edit-countries")?.value || "").split(/\s*[\/,，]\s*/).filter(Boolean),
      overview: $("#edit-overview")?.value.trim(),
      poster_url: $("#edit-poster")?.value.trim(),
    };
    setButtonLoading(button, true, "保存中…");
    try {
      const data = await api(`/api/works/${state.detail.id}`, { method: "PATCH", body });
      state.detail = data.work;
      state.tab = "info";
      renderDrawer();
      await loadWorks();
      toast("本地元数据已保存", "ok");
    } catch (error) {
      toast(error.message, "bad", "保存失败");
    } finally {
      setButtonLoading(button, false);
    }
  }

  async function previewNfo() {
    if (!state.detail) return;
    try {
      const response = await fetch(`/api/works/${state.detail.id}/nfo`);
      const text = await response.text();
      openModal(`
        <div class="modal-head">
          <div><h3>NFO 预览</h3><p>${esc(titleOf(state.detail))}</p></div>
          <button class="icon-btn" data-action="close-modal">${icon("close")}</button>
        </div>
        <div class="modal-body"><pre class="nfo-pre">${esc(text)}</pre></div>
        <div class="modal-foot"><button class="btn ghost" data-action="close-modal">关闭</button></div>
      `);
    } catch (error) {
      toast(error.message, "bad", "读取 NFO 失败");
    }
  }

  async function organizePreview() {
    if (!state.detail) return;
    try {
      const data = await api(`/api/works/${state.detail.id}/organize`, { method: "POST", body: { dry_run: true } });
      const plan = data.plan;
      if (!plan.moves.length) {
        toast("文件已经在这个位置，不需要移动", "ok", "无需整理");
        return;
      }
      openModal(`
        <div class="modal-head">
          <div><h3>整理预览</h3><p>将移动 ${plan.move_count} 个文件到 ${esc(plan.target_root)}</p></div>
          <button class="icon-btn" data-action="close-modal">${icon("close")}</button>
        </div>
        <div class="modal-body">
          <div class="notice">会移动视频、NFO 和已有图片。确认前请确保没有其他程序正在使用这些文件。</div>
          <div class="plan-list">${plan.moves.map((move) => `
            <div class="plan-row"><b>${esc(move.role)}</b><span title="${esc(move.src)}">${esc(move.src)} <span class="arrow">→</span> ${esc(move.dest)}</span></div>
          `).join("")}</div>
        </div>
        <div class="modal-foot">
          <button class="btn ghost" data-action="close-modal">取消</button>
          <button class="btn primary" data-action="confirm-organize">确认移动</button>
        </div>
      `);
    } catch (error) {
      toast(error.message, "bad", "生成整理预览失败");
    }
  }

  async function organizeConfirm() {
    if (!state.detail) return;
    try {
      const data = await api(`/api/works/${state.detail.id}/organize`, { method: "POST", body: { dry_run: false } });
      closeModal();
      state.detail = data.work;
      state.tab = "files";
      renderDrawer();
      await loadWorks();
      toast(`已移动 ${data.result.moved.length} 个文件`, "ok", "整理完成");
    } catch (error) {
      toast(error.message, "bad", "整理失败");
    }
  }

  function confirmDanger(title, text, action, dangerLabel = "确认") {
    state.confirmAction = action;
    openModal(`
      <div class="modal-head"><div><h3>${esc(title)}</h3><p>${esc(text)}</p></div><button class="icon-btn" data-action="close-modal">${icon("close")}</button></div>
      <div class="modal-foot">
        <button class="btn ghost" data-action="close-modal">取消</button>
        <button class="btn danger" data-action="run-confirm">${esc(dangerLabel)}</button>
      </div>
    `, true);
  }

  async function runConfirm() {
    const action = state.confirmAction;
    state.confirmAction = null;
    closeModal();
    if (action) await action();
  }

  function openModal(html, small = false) {
    $("#modal-root").innerHTML = `<div class="modal-scrim" data-action="close-modal"><div class="modal ${small ? "small" : ""}" data-modal-panel>${html}</div></div>`;
    document.body.classList.add("modal-open");
  }

  function closeModal() {
    $("#modal-root").innerHTML = "";
    document.body.classList.remove("modal-open");
  }

  function confirmCloseScrim(event) {
    const target = event.target;
    if (target && target.classList && target.classList.contains("modal-scrim")) {
      closeModal();
    }
  }

  async function addFolderModal() {
    openModal(`
      <div class="modal-head">
        <div><h3>添加媒体文件夹</h3><p>支持电影和剧集，子目录会自动扫描。</p></div>
        <button class="icon-btn" data-action="close-modal">${icon("close")}</button>
      </div>
      <div class="modal-body">
        <div class="form-row">
          <label class="label">文件夹路径</label>
          <input class="input" id="folder-path" placeholder="例如 D:\\Media\\Movies" autofocus>
        </div>
        <div class="hint">轻刮会递归扫描 mkv / mp4 / avi / ts 等视频文件，并跳过 sample、预告、菜单和 SPs/EXTRA 等特典目录。</div>
      </div>
      <div class="modal-foot">
        <button class="btn ghost" data-action="browse-folder">${icon("folder")}浏览…</button>
        <button class="btn ghost" data-action="close-modal">取消</button>
        <button class="btn primary" data-action="add-folder-submit">${icon("plus")}添加并扫描</button>
      </div>
    `, true);
  }

  async function browseFolder() {
    const button = $('[data-action="browse-folder"]');
    setButtonLoading(button, true, "选择中…");
    try {
      const data = await api("/api/dialog/folder", { method: "POST" });
      if (data.path) $("#folder-path").value = data.path;
    } catch (error) {
      toast(error.message, "warn", "无法打开文件选择器");
    } finally {
      setButtonLoading(button, false);
    }
  }

  async function browsePoster() {
    const button = $('[data-action="browse-poster"]');
    setButtonLoading(button, true, "选择中…");
    try {
      const data = await api("/api/dialog/file", { method: "POST" });
      if (data.path && $("#edit-poster")) $("#edit-poster").value = data.path;
    } catch (error) {
      toast(error.message, "warn", "无法打开文件选择器");
    } finally {
      setButtonLoading(button, false);
    }
  }

  async function addFolderSubmit() {
    const path = $("#folder-path")?.value.trim();
    if (!path) { toast("请输入文件夹路径", "warn"); return; }
    const button = $('[data-action="add-folder-submit"]');
    setButtonLoading(button, true, "扫描中…");
    try {
      const folderData = await api("/api/folders", { method: "POST", body: { path } });
      const scanData = await api(`/api/folders/${folderData.folder.id}/scan`, { method: "POST" });
      closeModal();
      await bootstrap();
      await loadWorks();
      toast(`扫描到 ${scanData.works} 个作品 / ${scanData.files} 个文件`, "ok", "添加成功");
    } catch (error) {
      toast(error.message, "bad", "添加失败");
    } finally {
      setButtonLoading(button, false);
    }
  }

  async function scanAll() {
    const button = $('[data-action="scan"]');
    setButtonLoading(button, true, "扫描中…");
    try {
      const data = await api("/api/scan", { method: "POST", body: {} });
      await bootstrap();
      await loadWorks();
      const err = data.errors?.length ? `，${data.errors.length} 个目录跳过` : "";
      toast(`扫描到 ${data.works} 个作品 / ${data.files} 个文件${err}`, "ok", "扫描完成");
    } catch (error) {
      toast(error.message, "bad", "扫描失败");
    } finally {
      setButtonLoading(button, false);
    }
  }

  function autoModal() {
    const count = state.stats.new || 0;
    if (!count) {
      toast("没有未匹配的作品。可以先把筛选切到“待确认”。", "warn", "不用刮削");
      return;
    }
    openModal(`
      <div class="modal-head">
        <div><h3>一键刮削 ${count} 个作品</h3><p>自动搜索、打分，高置信度直接写入，低置信度标记为待确认。</p></div>
        <button class="icon-btn" data-action="close-modal">${icon("close")}</button>
      </div>
      <div class="modal-body">
        <div class="notice">每个作品会发起 1 次搜索 + 1 次详情请求。豆瓣请求有节流，第一次跑几十个作品需要一点时间。</div>
        <div class="hint">只处理“未匹配”。如果之前有失败或不确定的项目，先切到“待确认”手动处理更稳妥。</div>
      </div>
      <div class="modal-foot">
        <button class="btn ghost" data-action="close-modal">取消</button>
        <button class="btn primary" data-action="start-auto">${icon("spark")}开始</button>
      </div>
    `, true);
  }

  async function startAuto() {
    const button = $('[data-action="start-auto"]');
    setButtonLoading(button, true, "启动中…");
    try {
      const data = await api("/api/jobs/auto", { method: "POST", body: { status: "new" } });
      closeModal();
      state.job = data.job;
      showJob(data.job);
      pollJob(data.job.id);
    } catch (error) {
      toast(error.message, "bad", "启动失败");
    } finally {
      setButtonLoading(button, false);
    }
  }

  function showJob(job) {
    $("#jobbar").hidden = false;
    updateJobbar(job);
  }

  function updateJobbar(job) {
    const total = job.total || 0;
    const done = job.done || 0;
    $("#job-title").textContent = job.status === "running" ? "正在自动刮削" : job.status === "done" ? "刮削完成" : "任务状态";
    $("#job-message").textContent = job.message || "准备中…";
    $("#job-count").textContent = `${done} / ${total}`;
    $("#job-progress").style.width = `${total ? Math.min(100, (done / total) * 100) : 0}%`;
  }

  async function pollJob(jobId) {
    if (state.jobTimer) clearTimeout(state.jobTimer);
    const tick = async () => {
      try {
        const data = await api(`/api/jobs/${jobId}`);
        const job = data.job;
        state.job = job;
        showJob(job);
        if (["done", "cancelled"].includes(job.status)) {
          state.jobTimer = null;
          await bootstrap();
          await loadWorks();
          if (job.status === "done") {
            const counts = job.result?.counts || {};
            toast(job.message || "自动刮削完成", "ok", "一键刮削");
            if (counts.review) toast(`${counts.review} 个作品需要手动确认`, "warn", "还有待确认");
          } else {
            toast("任务已取消", "warn");
          }
          setTimeout(() => { $("#jobbar").hidden = true; }, 3500);
          return;
        }
        state.jobTimer = setTimeout(tick, 650);
      } catch (error) {
        state.jobTimer = setTimeout(tick, 1600);
      }
    };
    tick();
  }

  async function cancelJob() {
    if (!state.job) return;
    try {
      await api(`/api/jobs/${state.job.id}/cancel`, { method: "POST" });
      toast("正在取消…", "warn");
    } catch (error) {
      toast(error.message, "bad");
    }
  }

  async function loadDemo() {
    const button = $('[data-action="demo"]');
    setButtonLoading(button, true, "准备演示…");
    try {
      const data = await api("/api/demo", { method: "POST" });
      await bootstrap();
      await loadWorks();
      toast(`演示库已就绪：${data.stats.works} 个作品`, "ok", "可以开始玩了");
    } catch (error) {
      toast(error.message, "bad", "演示库创建失败");
    } finally {
      setButtonLoading(button, false);
    }
  }

  async function settingsModal() {
    try {
      const data = await api("/api/settings");
      state.settings = data.settings;
      const s = state.settings;
      openModal(`
        <div class="modal-head">
          <div><h3>设置</h3><p>全部本地保存，不上传任何账号信息。</p></div>
          <button class="icon-btn" data-action="close-modal">${icon("close")}</button>
        </div>
        <div class="modal-body">
          <div>
            <div class="section-title">刮削源</div>
            <div class="settings-grid">
              <div class="setting wide">
                <div class="setting-head">
                  <div><div class="setting-name">默认来源</div><div class="setting-desc">自动模式按“豆瓣 → 番库 → B站 → TMDb”顺序兜底。</div></div>
                  <select class="select compact" id="set-source">
                    <option value="auto" ${s.source === "auto" ? "selected" : ""}>自动</option>
                    <option value="douban" ${s.source === "douban" ? "selected" : ""}>豆瓣</option>
                    <option value="fankuhub" ${s.source === "fankuhub" ? "selected" : ""}>番库</option>
                    <option value="bilibili" ${s.source === "bilibili" ? "selected" : ""}>哔哩哔哩</option>
                    <option value="tmdb" ${s.source === "tmdb" ? "selected" : ""}>TMDb</option>
                  </select>
                </div>
              </div>
              <div class="setting">
                <div class="setting-head"><div><div class="setting-name">豆瓣</div><div class="setting-desc">免 Key，中文条目更全。</div></div>
                  <label class="switch"><input type="checkbox" id="set-douban" ${s.douban_enabled ? "checked" : ""}><i></i></label>
                </div>
              </div>
              <div class="setting">
                <div class="setting-head"><div><div class="setting-name">番库</div><div class="setting-desc">动漫优先，抓封面/剧名/集数/简介/年份。</div></div>
                  <label class="switch"><input type="checkbox" id="set-fankuhub" ${s.fankuhub_enabled ? "checked" : ""}><i></i></label>
                </div>
              </div>
              <div class="setting">
                <div class="setting-head"><div><div class="setting-name">哔哩哔哩</div><div class="setting-desc">番剧条目，含分集标题。</div></div>
                  <label class="switch"><input type="checkbox" id="set-bilibili" ${s.bilibili_enabled ? "checked" : ""}><i></i></label>
                </div>
              </div>
              <div class="setting">
                <div class="setting-head"><div><div class="setting-name">TMDb</div><div class="setting-desc">需要自己的 API Key。</div></div>
                  <label class="switch"><input type="checkbox" id="set-tmdb" ${s.tmdb_enabled ? "checked" : ""}><i></i></label>
                </div>
              </div>
              <div class="setting wide">
                <div class="form-row"><label class="label">TMDb API Key ${s.tmdb_key_set ? "（已保存，留空不修改）" : ""}</label><input class="input" id="set-tmdb-key" type="password" placeholder="${s.tmdb_key_set ? "•••••••• 已保存" : "粘贴 v3 API Key"}"></div>
                <div class="form-grid" style="margin-top:8px">
                  <div class="form-row"><label class="label">API 地址</label><input class="input" id="set-tmdb-base" value="${esc(s.tmdb_base || "")}"></div>
                  <div class="form-row"><label class="label">图片地址</label><input class="input" id="set-tmdb-image" value="${esc(s.tmdb_image_base || "")}"></div>
                </div>
                ${s.tmdb_key_set ? `<label class="hint" style="display:flex;gap:6px;align-items:center;margin-top:7px"><input type="checkbox" id="set-clear-key"> 清除已保存的 Key</label>` : ""}
              </div>
            </div>
          </div>
          <div>
            <div class="section-title">网络</div>
            <div class="settings-grid">
              <div class="form-row"><label class="label">HTTP 代理（可选）</label><input class="input" id="set-proxy" value="${esc(s.http_proxy || "")}" placeholder="http://127.0.0.1:7890"></div>
              <div class="form-row"><label class="label">图片代理前缀（可选）</label><input class="input" id="set-image-proxy" value="${esc(s.image_proxy || "")}" placeholder="https://你的代理/?url="></div>
              <div class="form-row"><label class="label">请求超时（秒）</label><input class="input" id="set-timeout" value="${esc(s.request_timeout || 20)}" inputmode="numeric"></div>
              <div class="form-row"><label class="label">语言</label><input class="input" id="set-language" value="${esc(s.language || "zh-CN")}"></div>
            </div>
          </div>
          <div>
            <div class="section-title">输出</div>
            <div class="settings-grid">
              <div class="setting"><div class="setting-head"><div><div class="setting-name">写入 NFO</div><div class="setting-desc">Jellyfin / Emby / Kodi 通用。</div></div><label class="switch"><input type="checkbox" id="set-write-nfo" ${s.write_nfo ? "checked" : ""}><i></i></label></div></div>
              <div class="setting"><div class="setting-head"><div><div class="setting-name">下载海报</div><div class="setting-desc">保存到视频旁边。</div></div><label class="switch"><input type="checkbox" id="set-images" ${s.download_images ? "checked" : ""}><i></i></label></div></div>
              <div class="setting"><div class="setting-head"><div><div class="setting-name">覆盖已有 NFO</div><div class="setting-desc">关闭时保留你手改过的 NFO。</div></div><label class="switch"><input type="checkbox" id="set-overwrite-nfo" ${s.overwrite_nfo ? "checked" : ""}><i></i></label></div></div>
              <div class="setting"><div class="setting-head"><div><div class="setting-name">覆盖已有海报</div><div class="setting-desc">重新刮削时更新图片。</div></div><label class="switch"><input type="checkbox" id="set-overwrite-art" ${s.overwrite_artwork ? "checked" : ""}><i></i></label></div></div>
              <div class="setting"><div class="setting-head"><div><div class="setting-name">读取媒体信息</div><div class="setting-desc">需要 ffprobe，写入分辨率/音轨。</div></div><label class="switch"><input type="checkbox" id="set-probe" ${s.probe_files ? "checked" : ""}><i></i></label></div></div>
              <div class="form-row"><label class="label">ffprobe 路径（可选）</label><input class="input" id="set-ffprobe" value="${esc(s.ffprobe_path || "")}" placeholder="留空自动查找"></div>
            </div>
          </div>
          <div>
            <div class="section-title">自动匹配</div>
            <div class="setting wide">
              <div class="setting-head"><div><div class="setting-name">置信度阈值</div><div class="setting-desc">低于该分数会标记为待确认，不会直接写入。</div></div><strong id="threshold-label" style="color:var(--accent)">${esc(s.auto_threshold || 82)}</strong></div>
              <input type="range" id="set-threshold" min="60" max="100" step="1" value="${esc(s.auto_threshold || 82)}" style="width:100%;margin-top:10px;accent-color:var(--accent)">
            </div>
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn ghost" data-action="close-modal">取消</button>
          <button class="btn primary" data-action="save-settings">${icon("check")}保存设置</button>
        </div>
      `);
      const range = $("#set-threshold");
      if (range) range.addEventListener("input", () => { $("#threshold-label").textContent = range.value; });
    } catch (error) {
      toast(error.message, "bad", "读取设置失败");
    }
  }

  async function saveSettings() {
    const button = $('[data-action="save-settings"]');
    setButtonLoading(button, true, "保存中…");
    const body = {
      source: $("#set-source")?.value,
      douban_enabled: $("#set-douban")?.checked,
      fankuhub_enabled: $("#set-fankuhub")?.checked,
      bilibili_enabled: $("#set-bilibili")?.checked,
      tmdb_enabled: $("#set-tmdb")?.checked,
      tmdb_api_key: $("#set-tmdb-key")?.value.trim() || "",
      tmdb_base: $("#set-tmdb-base")?.value.trim(),
      tmdb_image_base: $("#set-tmdb-image")?.value.trim(),
      http_proxy: $("#set-proxy")?.value.trim(),
      image_proxy: $("#set-image-proxy")?.value.trim(),
      request_timeout: Number($("#set-timeout")?.value || 20),
      language: $("#set-language")?.value.trim() || "zh-CN",
      write_nfo: $("#set-write-nfo")?.checked,
      download_images: $("#set-images")?.checked,
      overwrite_nfo: $("#set-overwrite-nfo")?.checked,
      overwrite_artwork: $("#set-overwrite-art")?.checked,
      probe_files: $("#set-probe")?.checked,
      ffprobe_path: $("#set-ffprobe")?.value.trim(),
      auto_threshold: Number($("#set-threshold")?.value || 82),
    };
    if ($("#set-clear-key")?.checked) body.clear_tmdb_key = true;
    try {
      const data = await api("/api/settings", { method: "PUT", body });
      state.settings = data.settings;
      closeModal();
      renderChrome();
      toast("设置已保存", "ok");
    } catch (error) {
      toast(error.message, "bad", "保存失败");
    } finally {
      setButtonLoading(button, false);
    }
  }

  function clearSearch() {
    state.q = "";
    $("#search-input").value = "";
    loadWorks();
  }

  function resetFilter() {
    state.filter = "all";
    state.status = "all";
    state.folder = null;
    state.seriesKey = null;
    state.seriesName = "";
    $("#status-filter").value = "all";
    $$(".filter").forEach((node) => node.classList.toggle("active", node.dataset.filter === "all"));
    loadWorks();
  }

  function selectFilter(filter) {
    state.filter = filter;
    state.seriesKey = null;
    state.seriesName = "";
    if (filter !== "attention" && filter !== "applied") state.status = "all";
    $("#status-filter").value = state.status;
    $$(".filter").forEach((node) => node.classList.toggle("active", node.dataset.filter === filter));
    loadWorks();
  }

  function selectSeries(key, name) {
    state.filter = "all";
    state.status = "all";
    state.seriesKey = key;
    state.seriesName = name;
    $("#status-filter").value = "all";
    $$(".filter").forEach((node) => node.classList.toggle("active", node.dataset.filter === "all"));
    loadWorks();
  }

  function removeFolder(id) {
    const folder = state.folders.find((f) => f.id === id);
    if (!folder) return;
    confirmDanger(
      "移除媒体库？",
      `只会从轻刮移除索引，不会删除磁盘上的文件。\n${folder.path}`,
      async () => {
        try {
          await api(`/api/folders/${id}`, { method: "DELETE" });
          if (state.folder === id) state.folder = null;
          await bootstrap();
          await loadWorks();
          toast("已移除索引", "ok");
        } catch (error) {
          toast(error.message, "bad");
        }
      },
      "移除",
    );
  }

  function deleteWork() {
    if (!state.detail) return;
    const work = state.detail;
    confirmDanger(
      "删除这个条目？",
      `只会删除轻刮里的记录和匹配信息，不会删除视频文件。\n${titleOf(work)}`,
      async () => {
        try {
          await api(`/api/works/${work.id}`, { method: "DELETE" });
          closeDrawer();
          await bootstrap();
          await loadWorks();
          toast("条目已删除", "ok");
        } catch (error) {
          toast(error.message, "bad");
        }
      },
      "删除",
    );
  }

  async function openFolder() {
    if (!state.detail) return;
    try {
      await api("/api/open", { method: "POST", body: { work_id: state.detail.id } });
    } catch (error) {
      toast(error.message, "warn", "无法打开目录");
    }
  }

  let searchTimer = null;
  function onSearchInput() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.q = $("#search-input").value.trim();
      loadWorks();
    }, 260);
  }

  async function onClick(event) {
    const actionNode = event.target.closest("[data-action]");
    if (!actionNode) return;
    const action = actionNode.dataset.action;
    if (action === "close-modal") {
      if (actionNode.classList.contains("modal-scrim") && event.target !== actionNode) return;
      closeModal();
      return;
    }
    switch (action) {
      case "filter": selectFilter(actionNode.dataset.filter); break;
      case "series":
        selectSeries(actionNode.dataset.key, actionNode.querySelector("span")?.textContent || "");
        break;
      case "folder":
        state.folder = Number(actionNode.dataset.id);
        state.seriesKey = null;
        state.seriesName = "";
        loadWorks();
        break;
      case "remove-folder":
        event.stopPropagation();
        removeFolder(Number(actionNode.dataset.id));
        break;
      case "add-folder": addFolderModal(); break;
      case "browse-folder": browseFolder(); break;
      case "browse-poster": browsePoster(); break;
      case "add-folder-submit": addFolderSubmit(); break;
      case "scan": scanAll(); break;
      case "auto": autoModal(); break;
      case "start-auto": startAuto(); break;
      case "settings": settingsModal(); break;
      case "save-settings": saveSettings(); break;
      case "demo": loadDemo(); break;
      case "refresh": bootstrap().then(loadWorks); break;
      case "open-work": openWork(Number(actionNode.dataset.id)); break;
      case "close-drawer": closeDrawer(); break;
      case "tab":
        state.tab = actionNode.dataset.tab;
        renderDrawer();
        break;
      case "search": searchWork(false); break;
      case "select-candidate": selectCandidate(Number(actionNode.dataset.index)); break;
      case "apply-selected": applySelected(); break;
      case "apply-existing": applyExisting(); break;
      case "save-manual": saveManual(); break;
      case "nfo": previewNfo(); break;
      case "organize": organizePreview(); break;
      case "confirm-organize": organizeConfirm(); break;
      case "open-folder": openFolder(); break;
      case "delete-work": deleteWork(); break;
      case "cancel-job": cancelJob(); break;
      case "clear-search": clearSearch(); break;
      case "reset-filter": resetFilter(); break;
      case "run-confirm": runConfirm(); break;
      default: break;
    }
  }

  function onKeydown(event) {
    if (event.key === "Escape") {
      if ($("#modal-root").innerHTML) closeModal();
      else if ($("#drawer").classList.contains("open")) closeDrawer();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      $("#search-input").focus();
    }
    if (event.key === "Enter" && event.target === $("#search-input")) {
      state.q = $("#search-input").value.trim();
      loadWorks();
    }
  }

  document.addEventListener("click", onClick);
  document.addEventListener("keydown", onKeydown);
  $("#search-input").addEventListener("input", onSearchInput);
  $("#status-filter").addEventListener("change", (event) => {
    state.status = event.target.value;
    if (state.status !== "all") state.filter = "all";
    $$(".filter").forEach((node) => node.classList.toggle("active", node.dataset.filter === "all" && state.status === "all"));
    loadWorks();
  });
  $("#drawer-scrim").addEventListener("click", closeDrawer);

  (async function init() {
    try {
      await bootstrap();
      await loadWorks();
    } catch (error) {
      toast(error.message, "bad", "启动失败");
    }
  })();
})();
