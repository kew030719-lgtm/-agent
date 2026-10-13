<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RefreshCw } from "@lucide/vue";
import { api, ApiError, displayTime, errorText } from "../api";
import type { KnowledgeJob } from "../types";

const props = defineProps<{ username: string; refreshToken: number }>();
const emit = defineEmits<{ settled: [] }>();
const jobs = ref<KnowledgeJob[]>([]);
const error = ref("");
const retryError = ref("");
const loading = ref(false);
const retrying = ref("");
const offset = ref(0);
const hasNext = ref(false);
const pageSize = 20;
const page = computed(() => Math.floor(offset.value / pageSize) + 1);
const active = new Map<string, KnowledgeJob>();
const observed = new Map<string, KnowledgeJob["status"]>();
let generation = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
let blocked = false;
const stageLabels: Record<string, string> = {
  queued: "排队中", dispatch_failed: "投递失败", parsing: "解析文件 / OCR",
  chunking: "切分内容", embedding: "生成向量", indexing: "写入索引",
  complete: "建库完成", completed: "建库完成", dispatch: "投递任务", failed: "建库失败",
};
const statusLabels: Record<KnowledgeJob["status"], string> = {
  queued: "等待处理", running: "处理中", succeeded: "已完成", failed: "失败",
};
function isActive(job: KnowledgeJob): boolean {
  return job.status === "queued" || job.status === "running";
}
function stageLabel(job: KnowledgeJob): string {
  return stageLabels[job.stage] || statusLabels[job.status] || "等待状态更新";
}
function stopTimer(): void {
  if (timer !== undefined) clearTimeout(timer);
  timer = undefined;
}
function schedule(): void {
  stopTimer();
  if (!blocked && active.size && document.visibilityState === "visible") {
    timer = setTimeout((): void => { void load(false); }, 3000);
  }
}
function track(job: KnowledgeJob): boolean {
  const previous = observed.get(job.id);
  observed.set(job.id, job.status);
  if (isActive(job)) active.set(job.id, job);
  else active.delete(job.id);
  return previous !== job.status && !isActive(job);
}
function reset(): void {
  generation += 1;
  stopTimer();
  active.clear();
  observed.clear();
  jobs.value = [];
  offset.value = 0;
  hasNext.value = false;
  loading.value = false;
  retrying.value = "";
  error.value = "";
  retryError.value = "";
}
async function load(showLoading = true): Promise<void> {
  if (loading.value || (blocked && !showLoading) || retrying.value) return;
  if (showLoading) blocked = false;
  const current = generation;
  const currentOffset = offset.value;
  loading.value = true;
  if (showLoading) error.value = "";
  stopTimer();
  try {
    const result = await api<KnowledgeJob[]>(`/knowledge-jobs?limit=${pageSize + 1}&offset=${currentOffset}`);
    if (current !== generation) return;
    let settled = false;
    const visibleIds = new Set(result.map((job) => job.id));
    for (const job of result) settled = track(job) || settled;
    // Keep following known active tasks when the user pages through older results.
    for (const id of [...active.keys()].filter((id) => !visibleIds.has(id))) {
      try {
        const job = await api<KnowledgeJob>(`/knowledge-jobs/${encodeURIComponent(id)}`);
        if (current !== generation) return;
        settled = track(job) || settled;
      } catch (err) {
        if (current !== generation) return;
        if (err instanceof ApiError && err.status === 404) active.delete(id);
        else throw err;
      }
    }
    jobs.value = result.slice(0, pageSize);
    hasNext.value = result.length > pageSize;
    error.value = "";
    if (settled) emit("settled");
  } catch (err) {
    if (current !== generation) return;
    if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
      jobs.value = [];
      active.clear();
      blocked = true;
    }
    error.value = errorText(err);
  } finally {
    if (current === generation) {
      loading.value = false;
      schedule();
    }
  }
}
async function changePage(delta: number): Promise<void> {
  if (loading.value || retrying.value) return;
  offset.value = Math.max(0, offset.value + delta * pageSize);
  await load();
}
async function retry(job: KnowledgeJob): Promise<void> {
  if (retrying.value || loading.value || !job.can_retry) return;
  const current = generation;
  retrying.value = job.id;
  error.value = "";
  retryError.value = "";
  stopTimer();
  try {
    const result = await api<KnowledgeJob>(`/knowledge-jobs/${encodeURIComponent(job.id)}/retry`, { method: "POST" });
    if (current !== generation) return;
    track(result);
    jobs.value = jobs.value.map((item) => item.id === result.id ? result : item);
    emit("settled");
  } catch (err) {
    if (current === generation) retryError.value = errorText(err);
  } finally {
    if (current === generation) {
      retrying.value = "";
      await load(false);
    }
  }
}
function onFocus(): void {
  if (document.visibilityState === "visible") void load(false);
  else stopTimer();
}
function onSessionExpired(): void {
  blocked = true;
  reset();
}
watch(() => props.refreshToken, (): void => {
  if (retrying.value) return;
  generation += 1;
  loading.value = false;
  offset.value = 0;
  void load();
});
watch(() => props.username, (): void => {
  reset();
  blocked = false;
  void load();
});
onMounted((): void => {
  window.addEventListener("focus", onFocus);
  window.addEventListener("session-expired", onSessionExpired);
  document.addEventListener("visibilitychange", onFocus);
  void load();
});
onBeforeUnmount((): void => {
  reset();
  window.removeEventListener("focus", onFocus);
  window.removeEventListener("session-expired", onSessionExpired);
  document.removeEventListener("visibilitychange", onFocus);
});
</script>

<template>
  <section class="table-card" aria-label="知识库导入任务">
    <div class="table-toolbar">
      <div><strong>导入任务</strong><p class="muted task-hint">仅显示你发起的任务。有正在处理的任务时，每 3 秒更新一次。</p></div>
      <button class="secondary" :disabled="loading || !!retrying" @click="load()"><RefreshCw :size="16" />刷新</button>
    </div>
    <p v-if="error" class="notice error task-notice" role="alert">{{ error }}</p>
    <p v-if="retryError" class="notice error task-notice" role="alert">{{ retryError }}</p>
    <div v-if="loading && !jobs.length" class="empty-state">正在读取任务…</div>
    <div v-else-if="!jobs.length" class="empty-state">暂无导入任务。</div>
    <div v-else class="table-scroll">
      <table>
        <thead><tr><th>文件 / 任务</th><th>进度</th><th>更新时间</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="job in jobs" :key="job.id">
            <td><strong>{{ job.title || `知识文件 #${job.document_id}` }}</strong><small>{{ job.kind === 'reindex' ? '补建索引' : '导入资料' }} · 文件 #{{ job.document_id }}</small><small class="task-id">任务 {{ job.id }}</small></td>
            <td>
              <span class="tag">{{ statusLabels[job.status] }}</span><small>{{ job.status === 'failed' ? '失败阶段：' : '' }}{{ stageLabel(job) }}</small>
              <small v-if="job.total_chunks > 0">已处理 {{ job.processed_chunks }} / {{ job.total_chunks }} 个切片</small>
              <small v-else-if="job.status === 'running'">正在准备切片，数量待确定</small>
              <small v-if="job.attempts > 0">已执行 {{ job.attempts }} 次</small>
              <p v-if="job.error" class="task-error">{{ job.error }}</p>
            </td>
            <td>{{ displayTime(job.updated_at) }}</td>
            <td><button v-if="job.can_retry" class="secondary" :disabled="!!retrying || loading" @click="retry(job)">{{ retrying === job.id ? '正在重新提交…' : '重试' }}</button><span v-else class="muted">—</span></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="row-actions task-pagination">
      <button class="secondary" :disabled="offset === 0 || loading || !!retrying" @click="changePage(-1)">上一页</button>
      <span class="muted">第 {{ page }} 页</span>
      <button class="secondary" :disabled="!hasNext || loading || !!retrying" @click="changePage(1)">下一页</button>
    </div>
  </section>
</template>

<style scoped>
.task-hint { margin: 5px 0 0; font-size: 12px; }
.task-notice, .task-pagination { margin: 16px; }
.task-pagination { justify-content: flex-end; }
td small { display: block; margin-top: 5px; color: var(--muted); }
.task-id { overflow-wrap: anywhere; max-width: 260px; font-size: 11px; }
.task-error { max-width: 320px; margin: 8px 0 0; overflow-wrap: anywhere; color: #9c3935; }
</style>
