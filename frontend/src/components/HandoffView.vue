<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { RefreshCw } from "@lucide/vue";
import { api, ApiError, displayTime, errorText } from "../api";
import type { Handoff, User } from "../types";

const props = defineProps<{ user: User; initialId?: number }>();
const emit = defineEmits<{ changed: [] }>();
const canManage = computed(() => props.user.permissions.includes("handoff:manage"));
const scope = ref<"mine" | "queue">("mine");
const status = ref<"all" | "pending" | "resolved">("all");
const items = ref<Handoff[]>([]), selected = ref<Handoff | null>(null);
const detailElement = ref<HTMLElement | null>(null);
const error = ref(""), notice = ref(""), reply = ref("");
const loading = ref(false), busy = ref(false), confirming = ref(false);
const page = ref(0), hasMore = ref(false);
const unread = (item: Handoff): boolean => item.status === "resolved" && !!item.reply && !item.read_at;

async function load(): Promise<void> {
  items.value = [];
  hasMore.value = false;
  loading.value = true;
  error.value = "";
  try {
    const path = scope.value === "mine" ? "/handoffs/mine" : "/handoffs";
    const rows = await api<Handoff[]>(`${path}?status=${status.value}&limit=21&offset=${page.value * 20}`);
    items.value = rows.slice(0, 20);
    hasMore.value = rows.length > 20;
    emit("changed");
  } catch (err) {
    selected.value = null;
    confirming.value = false;
    reply.value = "";
    error.value = errorText(err);
  } finally {
    loading.value = false;
  }
}
async function changeScope(value: "mine" | "queue"): Promise<void> {
  scope.value = value;
  status.value = value === "mine" ? "all" : "pending";
  await resetList();
}
async function resetList(): Promise<void> {
  page.value = 0;
  selected.value = null;
  confirming.value = false;
  reply.value = "";
  notice.value = "";
  await load();
}
async function paginate(delta: number): Promise<void> {
  page.value += delta;
  selected.value = null;
  await load();
}
async function open(item: Pick<Handoff, "id">): Promise<void> {
  busy.value = true;
  error.value = "";
  selected.value = null;
  reply.value = "";
  confirming.value = false;
  try {
    selected.value = await api<Handoff>(`/handoffs/${item.id}`);
    if (item.id === props.initialId && canManage.value && selected.value.status === "pending" && scope.value !== "queue") {
      scope.value = "queue";
      status.value = "pending";
      page.value = 0;
      await load();
    }
  } catch (err) {
    if (err instanceof ApiError && (err.status === 403 || err.status === 404)) {
      items.value = [];
      error.value = "该人工事项已不存在或当前账号无权访问，请刷新列表。";
    } else error.value = errorText(err);
  } finally {
    busy.value = false;
  }
}
async function markRead(): Promise<void> {
  if (!selected.value || busy.value) return;
  const selectedId = selected.value.id;
  busy.value = true;
  error.value = "";
  try {
    await api<void>(`/handoffs/${selectedId}/read`, { method: "POST" });
    selected.value = null;
    selected.value = await api<Handoff>(`/handoffs/${selectedId}`);
    await load();
  } catch (err) {
    if (err instanceof ApiError && (err.status === 403 || err.status === 404)) {
      selected.value = null;
      items.value = [];
    }
    error.value = errorText(err);
  } finally {
    busy.value = false;
  }
}
async function resolve(): Promise<void> {
  if (!selected.value || !reply.value.trim() || busy.value) return;
  const selectedId = selected.value.id;
  busy.value = true;
  error.value = "";
  try {
    await api<void>(`/handoffs/${selectedId}/resolve`, { method: "POST", body: JSON.stringify({ reply: reply.value.trim() }) });
    selected.value = null;
    selected.value = await api<Handoff>(`/handoffs/${selectedId}`);
    notice.value = "回复已保存，员工可在“我的事项”中查看。";
    confirming.value = false;
    await load();
  } catch (err) {
    if (err instanceof ApiError && (err.status === 403 || err.status === 404)) {
      selected.value = null;
      items.value = [];
    }
    error.value = errorText(err);
    confirming.value = false;
  } finally {
    busy.value = false;
  }
}
onMounted(async (): Promise<void> => {
  await load();
  if (props.initialId !== undefined) {
    await open({ id: props.initialId });
    await nextTick();
    detailElement.value?.scrollIntoView({ block: "start" });
  }
});
</script>

<template>
  <section class="page-content">
    <div class="page-heading"><div><h1>人工协助</h1><p class="muted">查看转人工事项的处理进度与回复。</p></div>
      <button class="secondary" :disabled="loading || busy" @click="resetList"><RefreshCw :size="16" />刷新事项</button>
    </div>
    <div class="row-actions knowledge-tabs">
      <button :class="scope === 'mine' ? 'primary' : 'secondary'" :disabled="loading || busy" @click="changeScope('mine')">我的事项</button>
      <button v-if="canManage" :class="scope === 'queue' ? 'primary' : 'secondary'" :disabled="loading || busy" @click="changeScope('queue')">处理队列</button>
    </div>
    <div class="row-actions handoff-filter"><label for="handoff-status">处理状态</label>
      <select id="handoff-status" v-model="status" :disabled="loading || busy" @change="resetList"><option value="all">全部</option><option value="pending">待处理</option><option value="resolved">已处理</option></select>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <div v-if="loading" class="empty-state">正在读取事项…</div>
    <div v-else-if="!items.length" class="empty-state table-card"><h3>当前没有符合条件的事项</h3><p>需要人工协助时，可以在工作助手中说明问题并确认转人工。</p></div>
    <div v-else class="table-card table-scroll"><table class="handoff-table">
      <thead><tr><th>编号 / 状态</th><th>问题</th><th>发起人 / 时间</th><th>操作</th></tr></thead>
      <tbody><tr v-for="item in items" :key="item.id">
        <td>#{{ item.id }}<small class="cell-detail">{{ item.status === 'pending' ? '待处理' : '已处理' }}</small><span v-if="unread(item) && item.created_by === user.username" class="tag amber">新回复</span></td>
        <td class="handoff-question"><strong>{{ item.question }}</strong><p>{{ item.reason }}</p></td>
        <td>{{ item.created_by }}<small class="cell-detail">{{ displayTime(item.created_at) }}</small></td>
        <td><button class="secondary" :disabled="busy" @click="open(item)">{{ scope === 'queue' && item.status === 'pending' ? '填写回复' : '查看详情' }}</button></td>
      </tr></tbody>
    </table></div>
    <div class="row-actions handoff-filter"><button class="secondary" :disabled="!page || loading || busy" @click="paginate(-1)">上一页</button><span>第 {{ page + 1 }} 页</span><button class="secondary" :disabled="!hasMore || loading || busy" @click="paginate(1)">下一页</button></div>
    <section v-if="selected" ref="detailElement" class="handoff-detail" aria-label="人工事项详情">
      <div class="section-title"><h3>人工事项 #{{ selected.id }}</h3><button class="text-button" :disabled="busy" @click="selected = null">关闭详情</button></div>
      <p>{{ selected.created_by }} · {{ selected.status === 'pending' ? '待处理' : '已处理' }}</p><p class="handoff-copy">{{ selected.question }}</p>
      <template v-if="selected.status === 'resolved'">
        <h4>处理回复</h4><p class="handoff-copy">{{ selected.reply || '这是一条旧的已处理记录，未保存回复内容。' }}</p>
        <p class="muted">处理人：{{ selected.resolved_by || '未记录' }} · {{ selected.resolved_at ? displayTime(selected.resolved_at) : '时间未记录' }}</p>
        <button v-if="unread(selected) && selected.created_by === user.username" class="secondary" :disabled="busy" @click="markRead">标记已读</button>
        <p v-else-if="selected.read_at && selected.created_by === user.username" class="muted">已读 · {{ displayTime(selected.read_at) }}</p>
      </template>
      <div v-else-if="canManage && scope === 'queue'" class="handoff-reply-form">
        <label for="handoff-reply">给员工的处理回复（必填）</label><textarea id="handoff-reply" v-model="reply" :disabled="busy" maxlength="4000" rows="5" placeholder="说明核对结果、已完成的处理或员工接下来需要做什么"></textarea>
        <p class="muted">请先完成必要的业务处理。填写回复不会自动修改请假余额或办理其他业务。</p>
        <button v-if="!confirming" class="primary" :disabled="busy || !reply.trim()" @click="confirming = true">回复并结束事项</button>
        <div v-else class="notice"><p>确认将上述回复提供给 {{ selected.created_by }} 并结束此事项？提交后不可修改。</p><div class="row-actions"><button class="primary" :disabled="busy || !reply.trim()" @click="resolve">{{ busy ? '提交中…' : '确认提交回复' }}</button><button class="secondary" :disabled="busy" @click="confirming = false">返回修改</button></div></div>
      </div>
      <p v-else class="muted">事项已进入人工处理队列，处理后可在这里查看回复。</p>
    </section>
  </section>
</template>
