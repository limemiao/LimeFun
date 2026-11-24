const state = { data: null, query: "", year: "", season: "", category: "", local: "", favorite: "", status: "", sort: "name", favorites: new Set(), seasonSnapshot: null, seasonError: false };
const $ = (id) => document.getElementById(id);
const downloadStateKey = "limefun-download-status";

function getDownloadStatus() {
  try { return JSON.parse(localStorage.getItem(downloadStateKey) || "{}"); }
  catch (_) { return {}; }
}

function setNoDownloadNeeded(item) {
  const statuses = getDownloadStatus();
  statuses[String(item.id)] = { local_count: Number(item.local_count || 0), status: "none" };
  localStorage.setItem(downloadStateKey, JSON.stringify(statuses));
}

function isNoDownloadNeeded(item) {
  const record = getDownloadStatus()[String(item.id)];
  return Boolean(record && record.status === "none" && Number(record.local_count) === Number(item.local_count || 0));
}

function showToast(message) {
  const toast = $("browseToast");
  toast.textContent = message;
  toast.classList.add("is-visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("is-visible"), 3000);
}

function fillSelect(id, values, emptyLabel) {
  const select = $(id);
  const selectedValue = select.value;
  select.innerHTML = `<option value="">${emptyLabel}</option>`;
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  }
  if ([...select.options].some(option => option.value === selectedValue)) select.value = selectedValue;
}

function formatStatus(item) { return Number(item.vod_isend) ? "已完结" : "连载中"; }
function formatYear(item) { return item.filter_year || item.vod_year || "年份未知"; }
function formatClasses(item) { return (item.categories || []).slice(0, 3); }
function isFavorite(item) { return state.favorites.has(String(item.id)); }

async function loadFavorites() {
  const response = await fetch("/api/favorites", { cache: "no-store" });
  if (!response.ok) throw new Error(`收藏请求失败 (${response.status})`);
  const payload = await response.json();
  state.favorites = new Set((payload.ids || []).map(String));
}

async function setFavorite(item, favorite) {
  const response = await fetch("/api/favorites", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: String(item.id), favorite }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `收藏更新失败 (${response.status})`);
  if (favorite) state.favorites.add(String(item.id));
  else state.favorites.delete(String(item.id));
}

async function enqueueDownload(item, button) {
  if (item.local_only || !Number.isFinite(Number(item.id))) {
    showToast("这个番剧没有可用的在线下载清单");
    return;
  }
  button.disabled = true;
  const original = button.textContent;
  button.textContent = "读取集数…";
  try {
    const response = await fetch(`/api/download/options?vod_id=${Number(item.id)}&name=${encodeURIComponent(item.vod_name)}`, { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `集数请求失败 (${response.status})`);
    const episodes = payload.episodes || [];
    if (!episodes.some((episode) => episode.status !== "downloaded")) {
      setNoDownloadNeeded(item);
      render();
      showToast(episodes.length ? "清单中的集数本地都已存在，无需下载" : "清单里没有可下载的集数");
      return;
    }
    openEpisodePicker(item, episodes, button);
  } catch (error) {
    showToast(error.message || "下载请求失败");
    button.textContent = original;
  } finally {
    if (button.textContent === "读取集数…") button.textContent = original;
    button.disabled = false;
  }
}

function openEpisodePicker(item, episodes, sourceButton) {
  document.querySelector(".episode-picker-modal")?.remove();
  const modal = document.createElement("div");
  modal.className = "episode-picker-modal";
  const dialog = document.createElement("section");
  dialog.className = "episode-picker-dialog";
  dialog.setAttribute("role","dialog"); dialog.setAttribute("aria-modal","true"); dialog.setAttribute("aria-label",`选择《${item.vod_name}》的下载集数`);
  dialog.innerHTML = `<div class="episode-picker-head"><div><div class="kicker">DOWNLOAD EPISODES</div><h2></h2><p class="muted-line">选择要下载的集数，已下载和下载中的集数不可重复加入</p></div><button class="modal-close" type="button" title="关闭">×</button></div><div class="episode-picker-actions"><button class="text-button select-missing" type="button">选择未下载</button><button class="text-button select-all" type="button">全选可下载</button></div><div class="episode-picker-list"></div><div class="episode-picker-foot"><span class="muted-line picker-summary"></span><button class="text-button confirm-download" type="button">开始下载</button></div>`;
  dialog.querySelector("h2").textContent = item.vod_name;
  const list = dialog.querySelector(".episode-picker-list");
  for (const episode of episodes) {
    const row = document.createElement("label");
    row.className = `episode-choice status-${episode.status}`;
    const disabled = episode.status === "downloaded" || episode.status === "downloading" || episode.status === "paused";
    row.innerHTML = `<input type="checkbox"><span class="episode-choice-name"></span><span class="episode-choice-status"></span>`;
    const checkbox = row.querySelector("input");
    checkbox.value = episode.label;
    checkbox.disabled = disabled;
    checkbox.checked = !disabled;
    row.querySelector(".episode-choice-name").textContent = episode.label;
    row.querySelector(".episode-choice-status").textContent = ({ downloaded: "已下载", downloading: "正在下载", paused: "已暂停", failed: "下载失败 · 点击重试", missing: "未下载" })[episode.status] || episode.status;
    list.appendChild(row);
  }
  const updateSummary = () => { dialog.querySelector(".picker-summary").textContent = `已选择 ${list.querySelectorAll("input:checked").length} 集 / ${episodes.length} 集`; };
  dialog.querySelector(".select-missing").addEventListener("click", () => { list.querySelectorAll("input:not(:disabled)").forEach((input) => { input.checked = true; }); updateSummary(); });
  dialog.querySelector(".select-all").addEventListener("click", () => { list.querySelectorAll("input:not(:disabled)").forEach((input) => { input.checked = true; }); updateSummary(); });
  list.addEventListener("change", updateSummary);
  const previousOverflow = document.body.style.overflow;
  document.body.style.overflow = "hidden";
  const close = () => { modal.remove(); document.body.style.overflow = previousOverflow; sourceButton.focus(); };
  modal.addEventListener("keydown", event => {
    if (event.key === "Escape") { event.preventDefault(); close(); }
    if (event.key === "Tab") {
      const focusable = [...dialog.querySelectorAll("button:not(:disabled), input:not(:disabled)")];
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  dialog.querySelector(".modal-close").addEventListener("click", close);
  modal.addEventListener("click", (event) => { if (event.target === modal) close(); });
  dialog.querySelector(".confirm-download").addEventListener("click", async () => {
    const labels = [...list.querySelectorAll("input:checked")].map((input) => input.value);
    if (!labels.length) { showToast("请先选择要下载的集数"); return; }
    const confirm = dialog.querySelector(".confirm-download");
    confirm.disabled = true;
    try {
      const response = await fetch("/api/download/selected", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ vod_id: Number(item.id), vod_name: item.vod_name, episodes: labels }) });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || "下载请求失败");
      showToast(`已创建 ${result.created || 0} 个任务，重试 ${result.retried || 0} 个`);
      close();
      if (Number(result.created || 0) + Number(result.retried || 0) === 0) {
        setNoDownloadNeeded(item);
        render();
      } else {
        sourceButton.textContent = "已加入下载";
      }
    } catch (error) { showToast(error.message || "下载请求失败"); confirm.disabled = false; }
  });
  modal.appendChild(dialog);
  document.body.appendChild(modal);
  dialog.querySelector(".modal-close").focus();
  updateSummary();
}

function filteredItems() {
  const query = state.query.trim().toLocaleLowerCase();
  const items = state.data?.items || [];
  const result = items.filter((item) => {
    const matchesQuery = !query || item.vod_name.toLocaleLowerCase().includes(query) || String(item.id) === query;
    const matchesYear = !state.year || item.filter_year === state.year;
    const matchesSeason = !state.season || item.season === state.season;
    const matchesCategory = !state.category || (item.categories || []).includes(state.category);
    const matchesLocal = !state.local || (state.local === "yes" ? item.local : !item.local);
    const matchesFavorite = !state.favorite || (state.favorite === "yes" ? isFavorite(item) : !isFavorite(item));
    const matchesStatus = !state.status || (state.status === "end" ? Number(item.vod_isend) === 1 : Number(item.vod_isend) !== 1);
    return matchesQuery && matchesYear && matchesSeason && matchesCategory && matchesLocal && matchesFavorite && matchesStatus;
  });
  result.sort((left, right) => {
    if (state.sort === "id") return Number(left.id) - Number(right.id);
    if (state.sort === "year") return String(right.filter_year || "").localeCompare(String(left.filter_year || "")) || left.vod_name.localeCompare(right.vod_name, "zh-CN");
    return left.vod_name.localeCompare(right.vod_name, "zh-CN");
  });
  return result;
}

function renderActiveFilters() {
  const values = [];
  if (state.query) values.push(`搜索: ${state.query}`);
  if (state.year) values.push(`首播年: ${state.year}`);
  if (state.season) values.push(LimeSeasons.labels[state.season]);
  if (state.category) values.push(`类别: ${state.category}`);
  if (state.local) values.push(state.local === "yes" ? "本地已有" : "本地没有");
  if (state.favorite) values.push(state.favorite === "yes" ? "已收藏" : "未收藏");
  if (state.status) values.push(state.status === "end" ? "已完结" : "连载中");
  $("activeFilters").textContent = values.join(" · ");
}

function createCard(item) {
  const article = document.createElement("article");
  article.className = "catalog-card";
  const cover = document.createElement("div");
  cover.className = "catalog-cover";
  if (item.cover_url) {
    const image = document.createElement("img");
    image.loading = "lazy";
    image.alt = item.vod_name;
    image.src = item.cover_url;
    image.addEventListener("error", () => { image.remove(); cover.classList.add("cover-missing"); });
    cover.appendChild(image);
  } else {
    cover.classList.add("cover-missing");
  }
  const body = document.createElement("div");
  body.className = "catalog-card-body";
  const favorite = document.createElement("button");
  favorite.type = "button";
  favorite.className = `favorite-toggle${isFavorite(item) ? " is-active" : ""}`;
  favorite.setAttribute("aria-pressed", String(isFavorite(item)));
  favorite.textContent = isFavorite(item) ? "★ 已收藏" : "☆ 收藏番剧";
  favorite.addEventListener("click", async () => {
    const next = !isFavorite(item);
    favorite.disabled = true;
    try {
      await setFavorite(item, next);
      showToast(next ? "已加入收藏" : "已取消收藏");
      render();
    } catch (error) {
      showToast(error.message || "收藏更新失败");
      favorite.disabled = false;
    }
  });
  const title = document.createElement("h2");
  title.textContent = item.vod_name;
  const meta = document.createElement("div");
  meta.className = "catalog-meta";
  meta.textContent = `ID ${item.id} · ${formatYear(item)} · ${formatStatus(item)}`;
  const premiere = document.createElement("div");
  premiere.className = `season-label${item.season === 'unknown' ? ' is-unknown' : ''}`;
  premiere.textContent = state.seasonError ? '季度资料暂不可用' : LimeSeasons.labels[item.season];
  premiere.title = item.premiere_date ? `首播 ${item.premiere_date}${String(item.vod_year || '') !== item.filter_year ? ` · 来源年份 ${item.vod_year || '未知'}` : ''}` : '尚未确认首播日期';
  const remarks = document.createElement("div");
  remarks.className = "catalog-remarks";
  remarks.textContent = item.vod_remarks || "";
  const tags = document.createElement("div");
  tags.className = "catalog-tags";
  for (const category of formatClasses(item)) {
    const tag = document.createElement("span");
    tag.textContent = category;
    tags.appendChild(tag);
  }
  const footer = document.createElement("div");
  footer.className = "catalog-card-footer";
  const local = document.createElement("span");
  local.className = item.local ? "local-state is-present" : "local-state";
  local.textContent = item.local ? `本地已有 · ${item.local_count} 集` : "本地无文件";
  const actions = document.createElement("div");
  actions.className = "card-actions";
  const link = document.createElement("a");
  link.className = item.local ? "card-action" : "card-action is-disabled";
  link.textContent = item.local ? "打开播放页" : "无本地文件";
  // Pass the catalog id as well: the player resolves it against the ids the
  // server attaches to local series, which is exact unlike name matching.
  link.href = item.local ? `/player.html?vod=${encodeURIComponent(item.id)}&add=${encodeURIComponent(item.vod_name)}` : "#";
  if (!item.local) link.addEventListener("click", (event) => { event.preventDefault(); showToast("这个番剧在本地库中没有视频文件"); });
  const download = document.createElement("button");
  download.type = "button";
  download.className = "card-action card-download";
  download.textContent = item.local ? (isNoDownloadNeeded(item) ? "无需下载" : "更新下载") : "加入下载";
  download.title = item.local ? "下载缺失或新增集数" : "下载到对应番剧目录";
  if (item.local_only) {
    download.disabled = true;
    download.classList.add("is-disabled");
    download.textContent = "无在线清单";
  } else {
    download.addEventListener("click", () => enqueueDownload(item, download));
  }
  actions.append(link, download);
  footer.append(local, actions);
  body.append(favorite, title, meta, premiere, remarks, tags, footer);
  if (item.local) {
    const coverLink = Object.assign(document.createElement("a"), {className:"cover-link", href:link.href});
    coverLink.setAttribute("aria-label", `播放《${item.vod_name}》`);
    coverLink.append(cover); article.append(coverLink,body);
  } else article.append(cover, body);
  return article;
}

function render() {
  const items = filteredItems();
  LimeUI.syncFilters();
  const visibleItems = LimeUI.paginate(items, render);
  $("resultCount").textContent = `${items.length.toLocaleString()} 部`;
  renderActiveFilters();
  const grid = $("catalogGrid");
  grid.innerHTML = "";
  if (!items.length) {
    grid.innerHTML = '<div class="empty-state">没有符合当前筛选条件的番剧</div>';
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const item of visibleItems) fragment.appendChild(createCard(item));
  grid.appendChild(fragment);
}

function loadFacets() {
  const years = [...new Set(state.data.items.map(item => item.filter_year).filter(Boolean))].sort((a,b) => b.localeCompare(a));
  fillSelect("yearFilter", years, "全部年份");
  fillSelect("categoryFilter", state.data.facets.categories || [], "全部类别");
}

async function loadCatalog(refresh = false) {
  const response = await fetch(`/api/catalog${refresh ? "?refresh=1" : ""}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`目录请求失败 (${response.status})`);
  state.data = await response.json();
  state.data.items = state.data.items.map(item => LimeSeasons.classify(item, state.seasonSnapshot));
  loadFacets();
  const known = state.data.items.filter(item => item.season !== 'unknown').length;
  $("seasonNote").textContent = state.seasonError
    ? '本地季度资料读取失败，季度筛选暂不可用。刷新页面可重试。'
    : `按首播季度归类：1–3月 / 4–6月 / 7–9月 / 10–12月。已确认 ${known} 部，未知 ${state.data.items.length - known} 部；年份优先采用首播年。资料来自 bangumi-data（CC BY 4.0），已离线保存。`;
  $("seasonFilter").disabled = state.seasonError;
  $("catalogMeta").textContent = `${state.data.items.length.toLocaleString()} 部 · 更新时间 ${state.data.generated_at}`;
  render();
}

async function loadSyncStatus() {
  const response = await fetch("/api/catalog/sync-status", { cache: "no-store" });
  if (!response.ok) return;
  const status = await response.json();
  const label = $("remoteSyncStatus");
  if (status.running) label.textContent = "远程同步中…";
  else if (status.last_error) label.textContent = `同步失败：${status.last_error}`;
  else if (status.last_finished) label.textContent = `上次同步 ${new Date(status.last_finished * 1000).toLocaleString("zh-CN", { hour12: false })}`;
  else label.textContent = "尚未同步远程清单";
}

for (const [id, key] of [["catalogSearch", "query"], ["yearFilter", "year"], ["seasonFilter", "season"], ["categoryFilter", "category"], ["localFilter", "local"], ["favoriteFilter", "favorite"], ["statusFilter", "status"], ["sortFilter", "sort"]]) {
  $(id).addEventListener("input", (event) => { state[key] = event.target.value; LimeUI.page = 1; render(); });
}
$("catalogRefresh").addEventListener("click", async () => {
  try { $("catalogRefresh").disabled = true; await loadCatalog(true); showToast("目录和本地状态已刷新"); }
  catch (error) { showToast(error.message); }
  finally { $("catalogRefresh").disabled = false; }
});
$("remoteSync").addEventListener("click", async () => {
  try {
    $("remoteSync").disabled = true;
    const response = await fetch("/api/catalog/sync", { method: "POST" });
    if (!response.ok) throw new Error(`同步请求失败 (${response.status})`);
    showToast("已开始同步远程清单，完成后请刷新目录");
    await loadSyncStatus();
  } catch (error) { showToast(error.message || "同步失败"); }
  finally { $("remoteSync").disabled = false; }
});
async function initializeCatalog() {
  try {
    const response = await fetch('/static/season-data.json?v=20261119-season1');
    if (!response.ok) throw new Error('Season metadata unavailable');
    const snapshot = await response.json();
    if (snapshot.version !== 1 || !snapshot.items || Array.isArray(snapshot.items)) throw new Error('Invalid season metadata');
    state.seasonSnapshot = snapshot;
  } catch (_) { state.seasonError = true; }
  try { await loadFavorites(); }
  catch (_) { state.favorites = new Set(); }
  await loadCatalog();
}

initializeCatalog().catch((error) => { $("catalogMeta").textContent = error.message; showToast(error.message); });
loadSyncStatus().catch(() => {});
setInterval(() => { loadSyncStatus().catch(() => {}); }, 5000);

LimeUI.filters(render);
