<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { RefreshCw } from "@lucide/vue";
import { api, ApiError, displayTime, errorText } from "../api";
import type { WorkKind, WorkNotification, WorkTarget, WorkTodo } from "../workCenterTypes";

const emit = defineEmits<{ open: [target: WorkTarget]; changed: [] }>();
type Tab = "todos" | "notifications";
const tab = ref<Tab>("todos");
const unreadOnly = ref(true);
const todos = ref<WorkTodo[]>([]), notifications = ref<WorkNotification[]>([]);
const loading = ref(false), busy = ref(false), error = ref(""), notice = ref("");
const page = ref(0), hasMore = ref(false);
const kindLabels: Record<WorkKind, string> = { leave: "请假", expense: "报销", ticket: "IT 工单", handoff: "人工协助" };
let active = true;
let requestVersion = 0;

async function load(): Promise<void> {
  const version = ++requestVersion;
  loading.value = true;
  error.value = "";
  todos.value = [];
  notifications.value = [];
  hasMore.value = false;
  try {
    const query = `limit=21&offset=${page.value * 20}`;
    if (tab.value === "todos") {
      const rows = await api<WorkTodo[]>(`/work-center/todos?${query}`);
      if (!active || version !== requestVersion) return;
      todos.value = rows.slice(0, 20);
      hasMore.value = rows.length > 20;
    } else {
      const rows = await api<WorkNotification[]>(`/notifications?unread_only=${unreadOnly.value}&${query}`);
      if (!active || version !== requestVersion) return;
      notifications.value = rows.slice(0, 20);
      hasMore.value = rows.length > 20;
    }
    emit("changed");
  } catch (err) {
    if (active && version === requestVersion) error.value = errorText(err);
  } finally {
    if (active && version === requestVersion) loading.value = false;
  }
}
async function changeTab(value: Tab): Promise<void> {
  tab.value = value;
  page.value = 0;
  notice.value = "";
  await load();
}
async function changeFilter(): Promise<void> {
  page.value = 0;
  notice.value = "";
  await load();
}
async function paginate(delta: number): Promise<void> {
  page.value += delta;
  notice.value = "";
  await load();
}
function open(item: WorkTarget): void {
  emit("open", { kind: item.kind, record_id: item.record_id });
}
async function markRead(item: WorkNotification): Promise<void> {
  if (busy.value || loading.value || item.read_at) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await api<void>(`/notifications/${item.id}/read`, { method: "POST" });
    if (!active) return;
    notice.value = "通知已标记为已读，业务办理状态不受影响。";
    await load();
    if (active && !error.value && page.value > 0 && !notifications.value.length) {
      page.value--;
      await load();
    }
  } catch (err) {
    if (!active) return;
    if (err instanceof ApiError && (err.status === 403 || err.status === 404)) {
      notifications.value = [];
      hasMore.value = false;
      error.value = "该通知已不存在或当前账号无权访问，请刷新列表。";
      emit("changed");
    } else error.value = errorText(err);
  } finally {
    if (active) busy.value = false;
  }
}
onMounted(load);
onUnmounted((): void => { active = false; requestVersion++; });
</script>

<template>
  <section class="page-content">
    <div class="page-heading">
      <div><h1>待办与通知</h1><p class="muted">查看需要你处理的事项，以及申请和服务的最新进展。</p></div>
      <button class="secondary" :disabled="loading || busy" @click="load"><RefreshCw :size="16" />刷新</button>
    </div>
    <div class="row-actions knowledge-tabs" aria-label="待办与通知列表范围">
      <button :class="tab === 'todos' ? 'primary' : 'secondary'" :disabled="loading || busy" @click="changeTab('todos')">我的待办</button>
      <button :class="tab === 'notifications' ? 'primary' : 'secondary'" :disabled="loading || busy" @click="changeTab('notifications')">站内通知</button>
    </div>
    <p class="work-center-hint muted">待办按当前业务状态汇总，已处理事项自动移出；已读仅表示看过通知。</p>
    <div v-if="tab === 'notifications'" class="row-actions work-center-filter">
      <label for="notification-filter">通知范围</label>
      <select id="notification-filter" v-model="unreadOnly" :disabled="loading || busy" @change="changeFilter"><option :value="true">未读通知</option><option :value="false">全部通知</option></select>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="loading" class="empty-state">正在读取{{ tab === 'todos' ? '待办' : '通知' }}…</p>
    <template v-else-if="!error">
      <template v-if="tab === 'todos'">
        <div v-if="!todos.length" class="empty-state table-card"><h3>当前没有待办</h3><p>待你审批、处理或确认的事项会汇总在这里。</p></div>
        <div v-else class="table-card table-scroll"><table class="work-center-table">
          <thead><tr><th>事项 / 类型</th><th>内容</th><th>业务创建时间</th><th>操作</th></tr></thead>
          <tbody><tr v-for="item in todos" :key="item.key">
            <td><strong>{{ item.title }}</strong><small class="cell-detail">{{ kindLabels[item.kind] }} #{{ item.record_id }}</small></td>
            <td class="work-center-copy">{{ item.summary }}</td>
            <td class="work-center-time">{{ displayTime(item.created_at) }}</td>
            <td><button class="secondary" :disabled="busy" @click="open(item)">{{ item.action_label || '查看详情' }}</button></td>
          </tr></tbody>
        </table></div>
      </template>
      <template v-else>
        <div v-if="!notifications.length" class="empty-state table-card"><h3>{{ unreadOnly ? '当前没有未读通知' : '当前没有通知' }}</h3><p>审批流转、处理结果等提醒会显示在这里。</p></div>
        <div v-else class="table-card table-scroll"><table class="work-center-table">
          <thead><tr><th>通知 / 类型</th><th>通知时间</th><th>阅读状态</th><th>操作</th></tr></thead>
          <tbody><tr v-for="item in notifications" :key="item.id">
            <td><strong class="work-center-copy">{{ item.title }}</strong><small class="cell-detail">{{ kindLabels[item.kind] }} #{{ item.record_id }}</small></td>
            <td class="work-center-time">{{ displayTime(item.created_at) }}</td>
            <td>{{ item.read_at ? '已读' : '未读' }}<small v-if="item.read_at" class="cell-detail">{{ displayTime(item.read_at) }}</small></td>
            <td><div class="row-actions"><button class="secondary" :disabled="busy" @click="open(item)">查看详情</button><button v-if="!item.read_at" class="text-button" :disabled="busy" @click="markRead(item)">标记已读</button></div></td>
          </tr></tbody>
        </table></div>
      </template>
    </template>
    <div class="row-actions work-center-pagination"><button class="secondary" :disabled="page === 0 || loading || busy" @click="paginate(-1)">上一页</button><span>第 {{ page + 1 }} 页</span><button class="secondary" :disabled="!hasMore || loading || busy" @click="paginate(1)">下一页</button></div>
    <p class="page-footnote">进入详情时会重新校验访问权限。查看通知不会自动标记已读，也不会自动批准或结束业务。</p>
  </section>
</template>

<style scoped>
.work-center-hint { font-size: 13px; line-height: 1.8; margin: 16px 0; }
.work-center-filter { margin: 0 0 16px; }
.work-center-filter select { width: auto; min-width: 130px; }
.work-center-table { min-width: 660px; }
.work-center-copy { white-space: pre-wrap; overflow-wrap: anywhere; max-width: 420px; }
.work-center-time { white-space: nowrap; }
.work-center-pagination { margin-top: 18px; }
</style>
