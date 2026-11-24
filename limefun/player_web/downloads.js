const $ = (id) => document.getElementById(id);
let lastSnapshot = null;
let historyQuery = "";
const selected = {tasks: new Set(), history: new Set()};
const visibleIds = {tasks: [], history: []};
let deleting = false;
let loadSequence = 0;

function syncSelection(kind) {
  const ids = visibleIds[kind];
  const count = ids.filter(id => selected[kind].has(id)).length;
  const all = $(`${kind}SelectAll`);
  all.checked = ids.length > 0 && count === ids.length;
  all.indeterminate = count > 0 && count < ids.length;
  all.disabled = deleting || !ids.length;
  $(`${kind}SelectionCount`).textContent = `已选 ${selected[kind].size} 项`;
  $(`${kind}DeleteSelected`).disabled = deleting || !selected[kind].size;
}

function addSelection(row, kind, id, title, disabled = false) {
  id = String(id);
  const label = document.createElement('label');
  label.className = 'download-select';
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.checked = selected[kind].has(id);
  checkbox.disabled = deleting || disabled;
  checkbox.setAttribute('aria-label', `选择 ${title}`);
  checkbox.dataset.selectionId = id;
  checkbox.addEventListener('change', () => {
    if (checkbox.checked) selected[kind].add(id); else selected[kind].delete(id);
    row.classList.toggle('is-selected', checkbox.checked);
    syncSelection(kind);
  });
  label.append(checkbox, document.createTextNode('选择'));
  row.querySelector('.download-row-main').prepend(label);
  row.classList.toggle('is-selected', checkbox.checked);
}

async function deleteItems(kind, ids) {
  if (deleting || !ids.length) return;
  const message = kind === 'tasks'
    ? `删除所选 ${ids.length} 个任务？将停止下载并清理未完成的临时文件，完整视频会保留。`
    : `删除所选 ${ids.length} 条下载记录？视频文件会保留。`;
  if (!window.confirm(message)) return;
  deleting = true;
  ++loadSequence; // Ignore any polling response issued before this mutation.
  render(lastSnapshot);
  try {
    let removed = 0, pending = 0, missing = 0;
    const errors = [];
    for (let start = 0; start < ids.length; start += 500) {
      const response = await fetch(`/api/download/${kind}/delete`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({ids:ids.slice(start, start + 500)}),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || '删除失败，请重新打开 LimeFun 后重试');
      const done = [...(result.deleted || []), ...(result.pending || []), ...(result.missing || [])];
      done.forEach(id => selected[kind].delete(String(id)));
      removed += (result.deleted || []).length;
      pending += (result.pending || []).length;
      missing += (result.missing || []).length;
      errors.push(...Object.values(result.errors || {}));
    }
    showToast(`已删除 ${removed} 项${pending ? `，${pending} 项正在停止并清理` : ''}${missing ? `，${missing} 项已结束或不存在` : ''}${errors.length ? `；${errors.length} 项失败：${errors[0]}` : ''}`);
  } catch (error) { showToast(error.message || '删除失败'); }
  finally {
    deleting = false;
    if (lastSnapshot) render(lastSnapshot);
    await loadDownloads().catch(() => {});
  }
}

function showToast(message) {
  const toast = $("downloadToast");
  toast.textContent = message;
  toast.classList.add("is-visible");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("is-visible"), 3000);
}

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function formatTime(value) {
  if (!value) return "-";
  return new Date(Number(value) * 1000).toLocaleString("zh-CN", { hour12: false });
}

function statusLabel(status) {
  return ({ queued: "排队中", downloading: "下载中", pausing: "暂停中", paused: "已暂停", completed: "已完成", failed: "失败", deleting: "正在删除", delete_failed: "清理失败" })[status] || status || "未知";
}

async function updateTask(task, action, button, successMessage) {
  button.disabled = true;
  try {
    const response = await fetch(`/api/download/${encodeURIComponent(task.id)}/${action}`, { method: "POST" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || "操作失败");
    showToast(successMessage);
    await loadDownloads();
  } catch (error) { showToast(error.message || "操作失败"); button.disabled = false; }
}

function renderTask(task) {
  const row = document.createElement("article");
  row.className = `download-row status-${task.status}`;
  const progress = Math.max(0, Math.min(1, Number(task.progress) || 0));
  const percent = task.status === "completed" ? 100 : Math.round(progress * 100);
  row.innerHTML = `<div class="download-row-main"><div class="download-title"></div><div class="download-subtitle"></div><div class="progress-track"><div class="progress-value"></div></div><div class="download-detail"></div></div><div class="download-row-side"><span class="download-status"></span><button class="task-action-button" type="button"></button></div>`;
  row.querySelector(".download-title").textContent = `${task.vod_name || "未知番剧"} · ${task.episode_name || task.filename || ""}`;
  row.querySelector(".download-subtitle").textContent = task.filename || "";
  row.querySelector(".progress-value").style.width = `${percent}%`;
  const speed = task.status === "downloading" ? ` · ${formatBytes(task.speed)}/s` : "";
  row.querySelector(".download-detail").textContent = task.total ? `${formatBytes(task.downloaded)} / ${formatBytes(task.total)} · ${percent}%${speed}` : `${percent}%${speed}`;
  row.querySelector(".download-status").textContent = statusLabel(task.status);
  const action = row.querySelector(".task-action-button");
  if (task.status === "downloading" || task.status === "queued") {
    action.textContent = "暂停";
    action.classList.add("pause-button");
    action.addEventListener("click", () => updateTask(task, "pause", action, task.status === "downloading" ? "正在安全暂停" : "任务已暂停"));
  } else if (task.status === "paused") {
    action.textContent = "继续";
    action.classList.add("resume-button");
    action.addEventListener("click", () => updateTask(task, "resume", action, "任务已移至队首"));
  } else if (task.status === "failed") {
    action.textContent = "重试";
    action.classList.add("retry-button");
    action.addEventListener("click", () => updateTask(task, "retry", action, "任务已移至队首"));
  } else {
    action.hidden = true;
  }
  if (task.error) row.querySelector('.download-detail').textContent += ` · ${task.error}`;
  const remove = document.createElement('button');
  remove.className = 'record-delete-button';
  remove.type = 'button';
  remove.textContent = task.status === 'deleting' ? '正在清理…' : task.status === 'delete_failed' ? '重试删除' : '删除任务';
  remove.disabled = deleting || task.status === 'deleting';
  remove.addEventListener('click', () => deleteItems('tasks', [String(task.id)]));
  row.querySelector('.download-row-side').append(remove);
  action.disabled = deleting;
  addSelection(row, 'tasks', task.id, `${task.vod_name} ${task.episode_name}`, task.status === 'deleting');
  return row;
}

function renderHistory(item, retryable) {
  const row = document.createElement("article");
  row.className = `download-row history-row status-${item.status}`;
  row.innerHTML = `<div class="download-row-main"><div class="download-title"></div><div class="download-subtitle"></div><div class="download-detail"></div></div><div class="download-row-side history-row-side"><span class="download-status"></span><time></time><div class="history-actions"><button class="retry-button history-retry-button" type="button">重试</button><button class="record-delete-button" type="button">删除记录</button></div></div>`;
  row.querySelector(".download-title").textContent = `${item.vod_name || "未知番剧"} · ${item.episode_name || item.filename || ""}`;
  row.querySelector(".download-subtitle").textContent = item.filename || "";
  row.querySelector(".download-detail").textContent = item.error ? `失败原因：${item.error}` : `已保存 ${formatBytes(item.size)}`;
  row.querySelector(".download-status").textContent = statusLabel(item.status);
  row.querySelector("time").textContent = formatTime(item.finished_at || item.created_at);
  const retry = row.querySelector(".history-retry-button");
  retry.hidden = !retryable;
  retry.addEventListener("click", async () => {
    retry.disabled = true;
    try {
      const response = await fetch(`/api/download/history/${encodeURIComponent(item.history_id)}/retry`, { method: "POST" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || "重试失败");
      showToast("任务已重新加入队列");
      await loadDownloads();
    } catch (error) { showToast(error.message || "重试失败"); retry.disabled = false; }
  });
  const remove = row.querySelector(".record-delete-button");
  remove.disabled = deleting;
  retry.disabled = deleting;
  remove.addEventListener('click', () => deleteItems('history', [String(item.history_id)]));
  addSelection(row, 'history', item.history_id, `${item.vod_name} ${item.episode_name}`);
  return row;
}

function render(snapshot) {
  const tasks = snapshot.tasks || [];
  const active = tasks
    .filter((task) => task.status !== "completed")
    .sort((left, right) => {
      const statusOrder = Number(right.status === "downloading") - Number(left.status === "downloading");
      const groupOrder = ({ downloading: 0, pausing: 1, paused: 2, queued: 3, failed: 4 })[left.status] - ({ downloading: 0, pausing: 1, paused: 2, queued: 3, failed: 4 })[right.status];
      return statusOrder || groupOrder || Number(left.queue_order || left.created_at || 0) - Number(right.queue_order || right.created_at || 0);
    });
  const history = [...(snapshot.history || [])].sort((left, right) => Number(right.finished_at || right.created_at || 0) - Number(left.finished_at || left.created_at || 0));
  for (const [kind, items] of [['tasks', active.filter(task => task.status !== 'deleting')], ['history', history]]) {
    const available = new Set(items.map(item => String(kind === 'tasks' ? item.id : item.history_id)));
    for (const id of selected[kind]) if (!available.has(id)) selected[kind].delete(id);
  }
  $("activeCount").textContent = active.length;
  $("historyCount").textContent = history.length;
  const workers = snapshot.workers || {};
  const workerMeta = workers.configured ? ` · 线程 ${Number(workers.alive) || 0}/${workers.configured} · 活动 ${Number(workers.busy) || 0}` : "";
  $("downloadMeta").textContent = `任务 ${active.length} 个 · 记录 ${history.length} 条${workerMeta} · ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
  const taskList = $("taskList"); taskList.innerHTML = "";
  if (!active.length) taskList.innerHTML = '<div class="empty-state">当前没有下载任务</div>';
  else active.forEach((task) => taskList.appendChild(renderTask(task)));
  const historyList = $("historyList"); historyList.innerHTML = "";
  const taskStatuses = new Map(tasks.map((task) => [String(task.id), task.status]));
  const query = historyQuery.trim().toLocaleLowerCase();
  const filteredHistory = history.filter((item) => !query || [item.vod_name, item.episode_name, item.filename, item.status, statusLabel(item.status), item.error].some((value) => String(value || "").toLocaleLowerCase().includes(query)));
  visibleIds.tasks = active.filter(task => task.status !== 'deleting').map(task => String(task.id));
  visibleIds.history = filteredHistory.map(item => String(item.history_id));
  syncSelection('tasks'); syncSelection('history');
  if (!filteredHistory.length) historyList.innerHTML = history.length ? '<div class="empty-state">没有符合搜索条件的下载记录</div>' : '<div class="empty-state">还没有通过下载中心创建的记录</div>';
  else filteredHistory.forEach((item) => historyList.appendChild(renderHistory(item, item.status === "failed" && taskStatuses.get(String(item.id)) === "failed")));
}

function switchView(view) {
  const showHistory = view === "history";
  $("tasksView").classList.toggle("is-hidden", showHistory);
  $("historyView").classList.toggle("is-hidden", !showHistory);
  document.querySelectorAll(".download-tab").forEach((tab) => {
    const active = tab.dataset.view === view;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  localStorage.setItem("limefun-download-view", view);
}

async function loadDownloads() {
  if (deleting) return;
  const sequence = ++loadSequence;
  const response = await fetch("/api/downloads", { cache: "no-store" });
  if (!response.ok) throw new Error(`任务请求失败 (${response.status})`);
  const snapshot = await response.json();
  if (deleting || sequence !== loadSequence) return;
  lastSnapshot = snapshot;
  render(lastSnapshot);
}

$("downloadRefresh").addEventListener("click", async () => {
  try { $("downloadRefresh").disabled = true; await loadDownloads(); showToast("下载状态已刷新"); }
  catch (error) { showToast(error.message || "刷新失败"); }
  finally { $("downloadRefresh").disabled = false; }
});
$("historySearch").addEventListener("input", (event) => {
  historyQuery = event.target.value;
  selected.history.clear();
  if (lastSnapshot) render(lastSnapshot);
});
for (const kind of ['tasks', 'history']) {
  $(`${kind}SelectAll`).addEventListener('change', event => {
    visibleIds[kind].forEach(id => event.target.checked ? selected[kind].add(id) : selected[kind].delete(id));
    if (lastSnapshot) render(lastSnapshot);
  });
  $(`${kind}DeleteSelected`).addEventListener('click', () => deleteItems(kind, [...selected[kind]]));
}
document.querySelectorAll(".download-tab").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.view)));
switchView(localStorage.getItem("limefun-download-view") || "tasks");
loadDownloads().catch((error) => { $("downloadMeta").textContent = error.message; showToast(error.message); });
setInterval(() => loadDownloads().catch(() => {}), 1500);
