const state = {
  library: null,
  playlist: JSON.parse(localStorage.getItem("limefun-playlist") || "[]"),
  rootId: localStorage.getItem("limefun-root") || "",
  seriesId: localStorage.getItem("limefun-series") || "",
  seasonId: localStorage.getItem("limefun-season") || "",
  episodeId: localStorage.getItem("limefun-episode") || "",
  playbackRate: Number(localStorage.getItem("limefun-playback-rate") || "1"),
  autoNext: localStorage.getItem("limefun-auto-next") !== "false",
};
let requestedEntryApplied = false;

const PLAYBACK_RATES = [0.5, 1, 1.25, 1.5, 2, 2.5, 3, 4];
if (!PLAYBACK_RATES.includes(state.playbackRate)) state.playbackRate = 1;

const params = new URLSearchParams(window.location.search);
const requestedVodId = params.get("vod") || "";
const requestedName = params.get("add") || params.get("name") || "";
const $ = (id) => document.getElementById(id);
const video = $("video");

function normalizedTitle(value) { return String(value).toLocaleLowerCase().replace(/[\s_·・:：\-—]/g, ""); }
function currentRoot() { return state.library?.roots.find((root) => root.id === state.rootId) || null; }
function currentSeries() { return currentRoot()?.series.find((series) => series.id === state.seriesId) || null; }
function currentSeason() { return currentSeries()?.seasons.find((season) => season.id === state.seasonId) || null; }
function formatSize(bytes) {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
  return `${value.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function showToast(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.add("is-visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("is-visible"), 2800);
}

function persist() {
  localStorage.setItem("limefun-playlist", JSON.stringify(state.playlist));
  localStorage.setItem("limefun-root", state.rootId);
  localStorage.setItem("limefun-series", state.seriesId);
  localStorage.setItem("limefun-season", state.seasonId);
  localStorage.setItem("limefun-episode", state.episodeId);
  localStorage.setItem("limefun-playback-rate", String(state.playbackRate));
}

function seriesTitles(series) { return [series.name, series.display_name].filter(Boolean); }

function seasonTitles(season) {
  const titles = [season.name, season.display_name].filter(Boolean);
  for (const title of [...titles]) {
    const parts = String(title).split(" / ");
    if (parts.length > 1) titles.push(parts[parts.length - 1]);
  }
  return [...new Set(titles.map(normalizedTitle).filter(Boolean))];
}

function seasonMatchTitles(series, season) {
  const titles = new Set(seasonTitles(season));
  for (const title of [...titles]) {
    titles.add(normalizedTitle(String(series.name || "") + title));
    if (series.display_name) titles.add(normalizedTitle(String(series.display_name) + title));
  }
  return titles;
}

function findRequestedSeason(series, name) {
  const wanted = normalizedTitle(name);
  if (!wanted) return null;
  // A card for the series directory itself has no more specific season.
  if (normalizedTitle(series.name) === wanted) return null;
  const seasons = series.seasons || [];
  const exact = seasons.find((season) => seasonMatchTitles(series, season).has(wanted));
  if (exact) return exact;
  // Flat series have a single "本目录" season: opening any card must use it.
  if (seasons.length === 1) return seasons[0];
  return null;
}

function numberFromText(text) {
  if (/^\d+$/.test(text)) return Number(text);
  const digits = { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
  if (text === "十") return 10;
  if (text.startsWith("十")) return 10 + (digits[text[1]] || 0);
  if (text.endsWith("十")) return (digits[text[0]] || 0) * 10;
  if (text.includes("十")) {
    const [tens, ones] = text.split("十");
    return (digits[tens] || 0) * 10 + (digits[ones] || 0);
  }
  return digits[text] || 0;
}

// Directory season labels sort naturally in the player: base directory, then
// numbered seasons, the final season, movies, and finally OVA/specials.
function seasonOrderKey(season) {
  const name = String(season?.name || "").split(" / ").pop().trim();
  if (/^(本目录|正片|TV版|本篇)$/i.test(name)) return [0, 0, name];
  if (/剧场版|映画|\bmovie\b/i.test(name)) return [3, 0, name];
  if (/\b(?:ova|oad|sp)\b|特别篇|总集篇|合集/i.test(name)) return [4, 0, name];
  if (/最终季|final\s*season/i.test(name)) return [2, 0, name];
  let match = /第\s*([0-9一二三四五六七八九十百]+)\s*[季期部]/.exec(name);
  if (match) return [1, numberFromText(match[1]), name];
  match = /(?:season|s)\s*0*(\d+)/i.exec(name);
  if (match) return [1, Number(match[1]), name];
  return [5, 0, name];
}

function compareSeasons(left, right) {
  const a = seasonOrderKey(left);
  const b = seasonOrderKey(right);
  return a[0] - b[0] || a[1] - b[1] || a[2].localeCompare(b[2], "zh-CN", { numeric: true });
}

function orderedSeasons(series) { return [...(series?.seasons || [])].sort(compareSeasons); }
function seasonLabel(season) {
  if (season?.display_name) return season.display_name;
  const parts = String(season?.name || "").split(" / ");
  const leaf = parts[parts.length - 1] || "";
  return leaf && leaf !== "本目录" ? leaf : "未分季";
}

// A catalog alias can be one season of a multi-season local series. Use the
// directory name for the playlist label in that case instead of a season title.
function seriesLabel(series) {
  const display = series.display_name;
  if (display && (series.seasons || []).some((season) => seasonTitles(season).includes(normalizedTitle(display)))) {
    return series.name;
  }
  return display || series.name;
}

function seriesCatalogIds(series) {
  if (Array.isArray(series.catalog_ids) && series.catalog_ids.length) return series.catalog_ids.map(String);
  return series.catalog_id != null ? [String(series.catalog_id)] : [];
}

function findSeriesById(vodId) {
  if (!vodId) return null;
  const wanted = String(vodId);
  for (const root of state.library.roots) {
    const series = root.series.find((item) => seriesCatalogIds(item).includes(wanted));
    if (series) return { root, series };
  }
  return null;
}

function findSeries(name) {
  const wanted = normalizedTitle(name);
  if (!wanted) return null;
  const entries = [];
  for (const root of state.library.roots) {
    for (const series of root.series) entries.push({ root, series });
  }
  for (const entry of entries) {
    if (seriesTitles(entry.series).some((value) => normalizedTitle(value) === wanted)) return entry;
  }
  // Fall back to containment, preferring a candidate that is a prefix of the
  // requested name (for example "BanG Dream!" for "BanG Dream! Ave Mujica")
  // over an unrelated series that merely shares a substring.
  let prefix = null;
  let loose = null;
  for (const entry of entries) {
    for (const value of seriesTitles(entry.series)) {
      const normalized = normalizedTitle(value);
      if (!normalized) continue;
      if (normalized.startsWith(wanted) || wanted.startsWith(normalized)) {
        if (!prefix || normalized.length > prefix.length) prefix = { ...entry, length: normalized.length };
      } else if (!loose && (normalized.includes(wanted) || wanted.includes(normalized))) {
        loose = entry;
      }
    }
  }
  return prefix || loose;
}

function ensureRequestedPlaylistEntry() {
  if (!requestedVodId && !requestedName) return;
  const found = findSeriesById(requestedVodId) || (requestedName ? findSeries(requestedName) : null);
  if (!found) return;
  const reference = { rootId: found.root.id, seriesId: found.series.id, name: found.series.name };
  const exists = state.playlist.some((item) => item.rootId === reference.rootId && item.seriesId === reference.seriesId);
  if (!exists) state.playlist.push(reference);
  state.rootId = reference.rootId;
  state.seriesId = reference.seriesId;
  const requestedSeason = requestedName ? findRequestedSeason(found.series, requestedName) : null;
  if (requestedSeason) {
    state.seasonId = requestedSeason.id;
    state.episodeId = requestedSeason.episodes[0]?.id || "";
  }
  persist();
}

function playlistEntries() {
  return state.playlist.map((reference) => {
    const root = state.library.roots.find((item) => item.id === reference.rootId);
    const series = root?.series.find((item) => item.id === reference.seriesId);
    return series ? { reference, root, series } : null;
  }).filter(Boolean);
}

function chooseValidSelection() {
  // Apply the incoming catalog link once; switching seasons or removing an
  // entry must not keep restoring the original URL selection.
  if (!requestedEntryApplied) {
    ensureRequestedPlaylistEntry();
    requestedEntryApplied = true;
  }
  const entries = playlistEntries();
  if (!entries.length) {
    state.rootId = state.seriesId = state.seasonId = state.episodeId = "";
    persist();
    return;
  }
  const active = entries.find((entry) => entry.root.id === state.rootId && entry.series.id === state.seriesId) || entries[0];
  state.rootId = active.root.id;
  state.seriesId = active.series.id;
  const seasons = orderedSeasons(active.series);
  const season = seasons.find((item) => item.id === state.seasonId) || seasons[0];
  state.seasonId = season?.id || "";
  const episode = season?.episodes.find((item) => item.id === state.episodeId) || season?.episodes[0];
  state.episodeId = episode?.id || "";
  persist();
}

function renderSeriesList() {
  const list = $("seriesList");
  const entries = playlistEntries();
  $("playlistCount").textContent = entries.length;
  list.innerHTML = "";
  if (!entries.length) {
    list.innerHTML = '<div class="empty-state">片单还是空的<br><span>从发现页打开本地番剧，即可加入片单</span></div>';
    return;
  }
  const query = normalizedTitle($("playlistSearch").value);
  const visible = entries.filter(entry => normalizedTitle(seriesLabel(entry.series)).includes(query));
  if (!visible.length) list.innerHTML = '<div class="empty-state">没有找到匹配的番剧</div>';
  for (const entry of visible) {
    const row = document.createElement("div");
    row.className = `series-item${entry.series.id === state.seriesId && entry.root.id === state.rootId ? " is-active" : ""}`;
    const select = document.createElement("button");
    select.type = "button";
    select.className = "series-select";
    select.innerHTML = '<span class="series-item-name"></span><span class="series-item-count"></span>';
    select.querySelector(".series-item-name").textContent = seriesLabel(entry.series);
    select.querySelector(".series-item-count").textContent = `${entry.series.count} 集`;
    select.title = seriesLabel(entry.series);
    select.setAttribute('aria-pressed', String(entry.series.id === state.seriesId && entry.root.id === state.rootId));
    select.addEventListener("click", () => {
      state.rootId = entry.root.id;
      state.seriesId = entry.series.id;
      const firstSeason = orderedSeasons(entry.series)[0];
      state.seasonId = firstSeason?.id || "";
      state.episodeId = firstSeason?.episodes[0]?.id || "";
      $("episodeSearch").value = "";
      persist();
      renderAll(false);
      switchRail('episodes');
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "series-remove";
    remove.title = "从播放列表删除";
    remove.setAttribute('aria-label', `从片单移除 ${seriesLabel(entry.series)}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      state.playlist = state.playlist.filter((item) => !(item.rootId === entry.root.id && item.seriesId === entry.series.id));
      if (state.rootId === entry.root.id && state.seriesId === entry.series.id) {
        state.rootId = state.seriesId = state.seasonId = state.episodeId = "";
      }
      persist();
      renderAll(false);
    });
    row.append(select, remove);
    list.appendChild(row);
  }
}

function renderSeasonSelect() {
  const series = currentSeries();
  const select = $("seasonSelect");
  select.innerHTML = "";
  for (const season of orderedSeasons(series)) {
    const option = document.createElement("option");
    option.value = season.id;
    option.textContent = `${seasonLabel(season)} · ${season.count} 集`;
    option.selected = season.id === state.seasonId;
    select.appendChild(option);
  }
  select.disabled = !series || !series.seasons.length;
}

function renderEpisodes() {
  const season = currentSeason();
  const grid = $("episodeGrid");
  grid.innerHTML = "";
  $("seasonTitle").textContent = season ? seasonLabel(season) : '未选择目录';
  $("episodeCount").textContent = season?.count || 0;
  $("episodeMatches").textContent = '';
  $("locateEpisode").disabled = !season?.episodes?.length;
  if (!season?.episodes?.length) {
    grid.innerHTML = '<div class="empty-state">这个目录没有可播放的视频</div>';
    return;
  }
  const query = $("episodeSearch").value.trim().toLocaleLowerCase();
  const episodes = season.episodes.filter(episode => !query || (/^\d+$/.test(query)
    ? episode.number === Number(query)
    : [episode.name, episode.filename].some(value => String(value || '').toLocaleLowerCase().includes(query))));
  $("episodeMatches").textContent = query ? `${episodes.length} / ${season.episodes.length} 集` : `${season.episodes.length} 集`;
  if (!episodes.length) grid.innerHTML = '<div class="empty-state">没有找到匹配集数</div>';
  for (const episode of episodes) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `episode-button${episode.id === state.episodeId ? " is-active" : ""}`;
    const label = episode.number == null ? episode.name : `第${episode.number}集`;
    button.innerHTML = '<span class="episode-number"></span><span class="episode-file"></span>';
    button.querySelector(".episode-number").textContent = label;
    button.querySelector(".episode-file").textContent = `${episode.filename} · ${formatSize(episode.size)}`;
    button.title = `${episode.filename} · ${formatSize(episode.size)}`;
    button.setAttribute('aria-label', `${label}${episode.id === state.episodeId ? '，当前集' : ''}`);
    if (episode.id === state.episodeId) button.setAttribute('aria-current', 'true');
    button.addEventListener("click", () => {
      playEpisode(episode.id, true);
      $("episodeGrid").querySelector('.is-active')?.focus({preventScroll:true});
    });
    grid.appendChild(button);
  }
}

function updatePlayer(autoPlay) {
  const season = currentSeason();
  const episode = season?.episodes.find((item) => item.id === state.episodeId);
  const series = currentSeries();
  video.defaultPlaybackRate = state.playbackRate;
  video.playbackRate = state.playbackRate;
  video.hidden = !episode;
  $("videoEmpty").classList.toggle("is-hidden", Boolean(episode));
  $("episodeTitle").textContent = episode ? episode.name : "还没有选择视频";
  $("episodeTitle").title = episode?.filename || '';
  const index = season?.episodes.findIndex(item => item.id === state.episodeId) ?? -1;
  $("episodePosition").textContent = index >= 0 ? `${index + 1} / ${season.episodes.length} 集` : '';
  $("prevButton").disabled = index <= 0;
  $("nextButton").disabled = index < 0 || index >= season.episodes.length - 1;
  $("playbackRateSelect").disabled = !episode;
  if (episode && video.dataset.episode !== episode.id) {
    video.dataset.episode = episode.id;
    video.src = episode.url;
    video.load();
    if (autoPlay) video.play().catch(() => {});
  } else if (!episode) {
    video.removeAttribute("src");
    video.load();
    delete video.dataset.episode;
  }
  updatePlaybackStatus();
  document.title = episode && series ? `${episode.name} · ${series.name} - LimeFun` : "LimeFun Player · 播放";
}

function playEpisode(id, autoPlay = false) {
  state.episodeId = id;
  persist();
  renderEpisodes();
  updatePlayer(autoPlay);
}

function moveEpisode(direction) {
  const episodes = currentSeason()?.episodes || [];
  const index = episodes.findIndex((item) => item.id === state.episodeId);
  const next = episodes[index + direction];
  if (next) playEpisode(next.id, true);
  else showToast(direction > 0 ? "已经是最后一集" : "已经是第一集");
}

function renderAll(autoPlay = false) {
  chooseValidSelection();
  renderSeriesList();
  const root = currentRoot();
  const series = currentSeries();
  $("libraryLabel").textContent = root?.label || "播放列表";
  $("seriesTitle").textContent = series ? seriesLabel(series) : "给自己一点观影时间。";
  $("seriesMeta").textContent = series ? `${series.seasons.length} 个季 / 目录 · 本地 ${series.count} 集` : "挑一部喜欢的番剧，把故事留给今晚。";
  renderSeasonSelect();
  renderEpisodes();
  updatePlayer(autoPlay);
}

async function loadLibrary(refresh = false) {
  const response = await fetch(`/api/library${refresh ? "?refresh=1" : ""}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`索引请求失败 (${response.status})`);
  state.library = await response.json();
  renderAll(false);
}

$("seasonSelect").addEventListener("change", (event) => {
  $("episodeSearch").value = "";
  state.seasonId = event.target.value;
  state.episodeId = currentSeason()?.episodes[0]?.id || "";
  persist();
  renderAll(false);
});
$("refreshButton").addEventListener("click", async () => {
  try {
    $("refreshButton").disabled = true;
    await loadLibrary(true);
    showToast("本地库已刷新");
  } catch (error) { showToast(error.message); }
  finally { $("refreshButton").disabled = false; }
});
$("prevButton").addEventListener("click", () => moveEpisode(-1));
$("nextButton").addEventListener("click", () => moveEpisode(1));
video.addEventListener("ended", () => {
  const episodes = currentSeason()?.episodes || [];
  const index = episodes.findIndex(episode => episode.id === state.episodeId);
  if (state.autoNext && index >= 0 && index < episodes.length - 1) moveEpisode(1);
});
$("playbackRateSelect").value = String(state.playbackRate);
$("playbackRateSelect").addEventListener("change", (event) => {
  const rate = Number(event.target.value);
  if (!PLAYBACK_RATES.includes(rate)) return;
  state.playbackRate = rate;
  video.defaultPlaybackRate = rate;
  video.playbackRate = rate;
  persist();
  showToast(`播放倍速：${rate}x`);
});

function switchRail(view) {
  for (const tab of document.querySelectorAll('.watch-tabs [role="tab"]')) {
    const active = tab.dataset.view === view;
    tab.setAttribute('aria-selected', String(active));
    $(tab.dataset.view + 'View').hidden = !active;
  }
}
document.querySelectorAll('.watch-tabs [role="tab"]').forEach(tab => tab.addEventListener('click', () => switchRail(tab.dataset.view)));
$("episodeSearch").addEventListener('input', renderEpisodes);
$("playlistSearch").addEventListener('input', renderSeriesList);
$("locateEpisode").addEventListener('click', () => {
  $("episodeSearch").value = '';
  renderEpisodes();
  const active = $("episodeGrid").querySelector('.is-active');
  active?.scrollIntoView({block:'nearest', behavior:'smooth'});
  active?.focus({preventScroll:true});
});
$("theaterButton").addEventListener('click', () => {
  const expanded = document.body.classList.toggle('is-theater');
  $("theaterButton").setAttribute('aria-pressed', String(expanded));
  $("theaterButton").querySelector('span').textContent = expanded ? '退出宽屏' : '宽屏模式';
});
$("autoNext").checked = state.autoNext;
$("autoNext").addEventListener('change', event => {
  state.autoNext = event.target.checked;
  localStorage.setItem('limefun-auto-next', String(state.autoNext));
});
function updatePlaybackStatus() {
  $("playbackStatus").textContent = !video.dataset.episode ? '待播放'
    : video.error ? '播放失败，请换一集或刷新本地库'
    : video.ended ? '已播完' : video.readyState < 2 ? '加载中'
    : video.paused ? '待播放 / 已暂停' : '正在播放';
}
for (const event of ['playing','pause','ended','loadstart','canplay','error','emptied']) video.addEventListener(event, updatePlaybackStatus);
video.addEventListener('waiting', () => { if (video.dataset.episode) $("playbackStatus").textContent = '缓冲中'; });

loadLibrary().catch((error) => {
  $("seriesTitle").textContent = "索引加载失败";
  showToast(error.message);
});
