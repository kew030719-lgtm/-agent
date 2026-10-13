<script setup lang="ts">
import { nextTick, onMounted, ref } from "vue";
import { RefreshCw } from "@lucide/vue";
import { api, ApiError, displayTime, errorText } from "../api";
import type { Ticket, TicketEvent, User } from "../types";

const props = defineProps<{ user: User; initialId?: number }>();
type View = "mine" | "queue" | "assigned";
type Action = TicketEvent["action"];
const view = ref<View>("mine");
const items = ref<Ticket[]>([]), selected = ref<Ticket | null>(null);
const detailElement = ref<HTMLElement | null>(null);
const loading = ref(false), busy = ref(false), error = ref(""), notice = ref("");
const comment = ref(""), action = ref<Action | null>(null);
const page = ref(0), hasMore = ref(false);
const labels: Record<Action, string> = { claim: "认领工单", resolve: "提交处理结果", confirm: "确认已解决", reopen: "退回继续处理" };

async function load(): Promise<void> {
  items.value = [];
  hasMore.value = false;
  loading.value = true;
  error.value = "";
  try {
    const rows = await api<Ticket[]>(`/tickets?view=${view.value}&limit=21&offset=${page.value * 20}`);
    hasMore.value = rows.length > 20;
    items.value = rows.slice(0, 20);
  } catch (err) {
    selected.value = null;
    action.value = null;
    error.value = errorText(err);
  }
  finally { loading.value = false; }
}
async function changeView(value: View): Promise<void> {
  view.value = value;
  page.value = 0;
  selected.value = null;
  action.value = null;
  notice.value = "";
  await load();
}
async function paginate(delta: number): Promise<void> {
  page.value += delta;
  selected.value = null;
  action.value = null;
  await load();
}
async function open(item: Pick<Ticket, "id">): Promise<void> {
  error.value = "";
  selected.value = null;
  action.value = null;
  comment.value = "";
  busy.value = true;
  try { selected.value = await api<Ticket>(`/tickets/${item.id}`); }
  catch (err) {
    if (err instanceof ApiError && (err.status === 403 || err.status === 404)) {
      items.value = [];
      error.value = "该工单已不存在或当前账号无权访问，请刷新列表。";
    } else error.value = errorText(err);
  }
  finally { busy.value = false; }
}
async function refresh(): Promise<void> {
  const selectedId = selected.value?.id;
  selected.value = null;
  action.value = null;
  comment.value = "";
  await load();
  if (selectedId !== undefined && !error.value) await open({ id: selectedId });
}
function prepare(value: Action): void {
  action.value = value;
  comment.value = "";
  notice.value = "";
}
async function submit(): Promise<void> {
  if (!selected.value || !action.value || busy.value) return;
  const pending = action.value;
  if ((pending === "resolve" || pending === "reopen") && !comment.value.trim()) {
    error.value = "请填写处理结果或未解决原因。";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    selected.value = await api<Ticket>(`/tickets/${selected.value.id}/${pending}`, {
      method: "POST", body: JSON.stringify({ version: selected.value.version, comment: comment.value.trim() }),
    });
    notice.value = pending === "resolve" ? "处理结果已保存，等待报修员工确认。" : `${labels[pending]}成功。`;
    action.value = null;
    comment.value = "";
    await load();
  } catch (err) {
    error.value = `${errorText(err)}；请刷新详情后再操作。`;
    action.value = null;
  } finally { busy.value = false; }
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
    <div class="page-heading">
      <div><h1>IT 工单</h1><p class="muted">跟进报修进度，查看处理结果并确认是否解决。</p></div>
      <button class="secondary" :disabled="loading || busy" @click="refresh"><RefreshCw :size="16" />刷新列表与详情</button>
    </div>
    <div class="row-actions knowledge-tabs" aria-label="工单列表范围">
      <button :class="view === 'mine' ? 'primary' : 'secondary'" :disabled="loading || busy" @click="changeView('mine')">我的报修</button>
      <template v-if="user.permissions.includes('ticket:manage')">
        <button :class="view === 'queue' ? 'primary' : 'secondary'" :disabled="loading || busy" @click="changeView('queue')">待认领</button>
        <button :class="view === 'assigned' ? 'primary' : 'secondary'" :disabled="loading || busy" @click="changeView('assigned')">我处理的</button>
      </template>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="loading" class="empty-state">正在读取工单…</p>
    <div v-else-if="!items.length" class="empty-state table-card"><h3>当前没有工单</h3><p>在工作助手中描述故障，核对摘要后即可提交报修。</p></div>
    <div v-else class="table-card table-scroll">
      <table><thead><tr><th>编号 / 时间</th><th>报修人 / 故障</th><th>状态 / 处理人</th><th>操作</th></tr></thead>
        <tbody><tr v-for="item in items" :key="item.id">
          <td>#{{ item.id }}<small class="cell-detail">{{ displayTime(item.created_at) }}</small></td>
          <td>{{ item.created_by }}<small class="cell-detail expense-description">{{ item.description }}</small></td>
          <td>{{ item.status_label }}<small class="cell-detail">{{ item.assigned_to || '尚未认领' }}</small></td>
          <td><button class="secondary" :disabled="busy" @click="open(item)">查看详情</button></td>
        </tr></tbody>
      </table>
    </div>
    <div class="row-actions expense-pagination">
      <button class="secondary" :disabled="page === 0 || loading || busy" @click="paginate(-1)">上一页</button><span>第 {{ page + 1 }} 页</span><button class="secondary" :disabled="!hasMore || loading || busy" @click="paginate(1)">下一页</button>
    </div>
    <section v-if="selected" ref="detailElement" class="expense-detail" aria-label="工单详情">
      <div class="section-title"><h3>工单 #{{ selected.id }}</h3><button class="text-button" :disabled="busy" @click="selected = null">关闭详情</button></div>
      <p>{{ selected.created_by }} · {{ selected.status_label }} · 处理人：{{ selected.assigned_to || '尚未认领' }}</p>
      <p class="expense-description">{{ selected.description }}</p>
      <div v-if="selected.resolution" class="notice"><strong>{{ selected.status === 'in_progress' ? '上次处理结果（已退回，仍在处理）' : '处理结果' }}</strong><p class="expense-description">{{ selected.resolution }}</p></div>
      <h4>处理记录</h4>
      <ol class="expense-history">
        <li><strong>{{ selected.created_by }} · 提交报修</strong><small class="cell-detail">{{ displayTime(selected.created_at) }}</small></li>
        <li v-for="event in selected.events" :key="event.id"><strong>{{ event.actor }} · {{ labels[event.action] }}</strong><small class="cell-detail">{{ displayTime(event.created_at) }}</small><p v-if="event.comment" class="expense-description">{{ event.comment }}</p></li>
      </ol>
      <div v-if="!action" class="row-actions">
        <button v-if="selected.can_claim" class="primary" :disabled="busy" @click="prepare('claim')">认领工单</button>
        <button v-if="selected.can_resolve" class="primary" :disabled="busy" @click="prepare('resolve')">填写处理结果</button>
        <button v-if="selected.can_confirm" class="primary" :disabled="busy" @click="prepare('confirm')">确认已解决</button>
        <button v-if="selected.can_reopen" class="secondary" :disabled="busy" @click="prepare('reopen')">仍未解决</button>
      </div>
      <div v-else class="expense-review">
        <template v-if="action === 'resolve' || action === 'reopen'">
          <label for="ticket-comment">{{ action === 'resolve' ? '处理结果（必填）' : '未解决原因（必填）' }}</label>
          <textarea id="ticket-comment" v-model="comment" :disabled="busy" maxlength="2000" rows="3" :placeholder="action === 'resolve' ? '说明故障原因、处理方式和验证结果' : '说明仍然存在的故障现象'"></textarea>
        </template>
        <div class="notice"><p>确认对工单 #{{ selected.id }} 执行“{{ labels[action] }}”？{{ action === 'confirm' ? '确认后工单将关闭。' : action === 'resolve' ? '提交后将等待报修员工确认。' : '' }}</p>
          <div class="row-actions"><button class="primary" :disabled="busy || ((action === 'resolve' || action === 'reopen') && !comment.trim())" @click="submit">{{ busy ? '提交中…' : '确认提交' }}</button><button class="secondary" :disabled="busy" @click="action = null">取消</button></div>
        </div>
      </div>
    </section>
    <p class="page-footnote">待处理 → 处理中 → 待员工确认 → 已关闭。未解决可退回原处理人；本人报修由其他 IT 人员处理。</p>
  </section>
</template>
