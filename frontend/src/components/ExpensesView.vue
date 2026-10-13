<script setup lang="ts">
import { nextTick, onMounted, ref } from "vue";
import { RefreshCw } from "@lucide/vue";
import { api, ApiError, displayTime, errorText } from "../api";
import type { Expense, User } from "../types";

const props = defineProps<{ user: User; initialId?: number }>();
type View = "mine" | "inbox" | "reviewed" | "all";
const view = ref<View>("mine");
const items = ref<Expense[]>([]), selected = ref<Expense | null>(null);
const detailElement = ref<HTMLElement | null>(null);
const loading = ref(false), busy = ref(false), error = ref(""), notice = ref("");
const comment = ref(""), decision = ref<"approved" | "rejected" | null>(null);
const page = ref(0), hasMore = ref(false);
const labels: Record<string, string> = { pending: "待审批", waiting: "待前级处理", approved: "已通过", rejected: "已驳回", superseded: "已结束 / 已重新分配" };

async function load(): Promise<void> {
  const selectedId = selected.value?.id;
  selected.value = null;
  items.value = [];
  hasMore.value = false;
  decision.value = null;
  comment.value = "";
  loading.value = true;
  error.value = "";
  try {
    const rows = await api<Expense[]>(`/expenses?view=${view.value}&limit=21&offset=${page.value * 20}`);
    hasMore.value = rows.length > 20;
    items.value = rows.slice(0, 20);
    if (selectedId !== undefined) selected.value = await api<Expense>(`/expenses/${selectedId}`);
  } catch (err) {
    if (err instanceof ApiError && (err.status === 403 || err.status === 404)) {
      items.value = [];
      hasMore.value = false;
      error.value = "当前列表或报销已不存在或当前账号无权访问，请重新选择列表。";
    } else error.value = errorText(err);
  } finally {
    loading.value = false;
  }
}
async function changeView(value: View): Promise<void> {
  view.value = value;
  page.value = 0;
  selected.value = null;
  notice.value = "";
  await load();
}
async function paginate(delta: number): Promise<void> {
  page.value += delta;
  await load();
}
async function open(item: Pick<Expense, "id">): Promise<void> {
  error.value = "";
  selected.value = null;
  decision.value = null;
  comment.value = "";
  busy.value = true;
  try {
    selected.value = await api<Expense>(`/expenses/${item.id}`);
  } catch (err) {
    if (err instanceof ApiError && (err.status === 403 || err.status === 404)) {
      items.value = [];
      error.value = "该报销已不存在或当前账号无权访问，请刷新列表。";
    } else error.value = errorText(err);
  } finally {
    busy.value = false;
  }
}
async function submitDecision(): Promise<void> {
  if (!selected.value || !decision.value || busy.value) return;
  const step = selected.value.steps.find((item) => item.round === selected.value?.approval_round && item.status === "pending");
  if (!step) return;
  if (decision.value === "rejected" && !comment.value.trim()) {
    error.value = "请填写驳回原因。";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    selected.value = await api<Expense>(`/expenses/${selected.value.id}/review`, {
      method: "POST", body: JSON.stringify({ step_id: step.id, decision: decision.value, comment: comment.value.trim() }),
    });
    notice.value = selected.value.status === "pending" ? "本级审批已通过，已流转至下一级。" : "审批结果已保存，员工可在“我的报销”中查看。";
    decision.value = null;
    await load();
  } catch (err) {
    error.value = errorText(err);
    decision.value = null;
  } finally {
    busy.value = false;
  }
}
async function reassign(): Promise<void> {
  if (!selected.value || busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    selected.value = await api<Expense>(`/expenses/${selected.value.id}/reassign`, { method: "POST" });
    notice.value = selected.value.routing_issue || "已按当前汇报关系重新分配，将从第一级重新审批。";
    await load();
  } catch (err) {
    error.value = errorText(err);
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
    <div class="page-heading">
      <div><h1>报销与审批</h1><p class="muted">查看申请进度，处理分配给你的审批。通过审批后仍需另行安排付款。</p></div>
      <button class="secondary" :disabled="loading || busy" @click="load"><RefreshCw :size="16" />刷新列表与详情</button>
    </div>
    <div class="row-actions knowledge-tabs" aria-label="报销列表范围">
      <button :class="view === 'mine' ? 'primary' : 'secondary'" :disabled="loading || busy" @click="changeView('mine')">我的报销</button>
      <button :class="view === 'inbox' ? 'primary' : 'secondary'" :disabled="loading || busy" @click="changeView('inbox')">待我审批</button>
      <button :class="view === 'reviewed' ? 'primary' : 'secondary'" :disabled="loading || busy" @click="changeView('reviewed')">我已审批</button>
      <button v-if="user.permissions.includes('expense:read_all')" :class="view === 'all' ? 'primary' : 'secondary'" :disabled="loading || busy" @click="changeView('all')">全部报销</button>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="loading" class="empty-state">正在读取申请…</p>
    <div v-else-if="!items.length" class="empty-state table-card"><h3>当前没有申请</h3><p>在工作助手中说明报销金额和事由，确认后即可提交。</p></div>
    <div v-else class="table-card table-scroll">
      <table><thead><tr><th>编号 / 时间</th><th>申请人 / 事由</th><th>金额</th><th>状态 / 当前审批人</th><th>操作</th></tr></thead>
        <tbody><tr v-for="item in items" :key="item.id">
          <td>#{{ item.id }}<small class="cell-detail">{{ displayTime(item.created_at) }}</small></td>
          <td>{{ item.created_by }}<small class="cell-detail expense-description">{{ item.description }}</small></td>
          <td class="expense-amount">¥{{ item.amount }}</td>
          <td>{{ item.routing_issue ? '待配置审批人' : labels[item.status] }}<small class="cell-detail">{{ item.current_approver || '—' }}</small></td>
          <td><button class="secondary" :disabled="busy" @click="open(item)">{{ item.can_review ? '查看并审批' : '查看详情' }}</button></td>
        </tr></tbody>
      </table>
    </div>
    <div class="row-actions expense-pagination">
      <button class="secondary" :disabled="page === 0 || loading || busy" @click="paginate(-1)">上一页</button><span>第 {{ page + 1 }} 页</span><button class="secondary" :disabled="!hasMore || loading || busy" @click="paginate(1)">下一页</button>
    </div>
    <section v-if="selected" ref="detailElement" class="expense-detail" aria-label="报销详情">
      <div class="section-title"><h3>报销 #{{ selected.id }}</h3><button class="text-button" :disabled="busy" @click="selected = null">关闭详情</button></div>
      <p><strong>¥{{ selected.amount }}</strong> · {{ selected.created_by }} · {{ labels[selected.status] }}</p>
      <p class="expense-description">{{ selected.description }}</p>
      <div v-if="selected.routing_issue" class="notice">
        <p>{{ selected.routing_issue }}</p>
        <button v-if="selected.can_reassign" class="secondary" :disabled="busy" @click="reassign">按当前组织关系重新匹配</button>
      </div>
      <h4>审批记录</h4>
      <ol v-if="selected.steps.length" class="expense-history"><li v-for="step in selected.steps" :key="step.id">
        <strong>第 {{ step.round }} 轮 · 第 {{ step.position }} 级 · {{ step.approver }}</strong> — {{ labels[step.status] }}
        <small v-if="step.reviewed_at" class="cell-detail">{{ displayTime(step.reviewed_at) }}</small><p v-if="step.comment" class="expense-description">{{ step.comment }}</p>
      </li></ol>
      <p v-else class="muted">尚未分配审批人，请补全组织关系后重新匹配。</p>
      <div v-if="selected.can_review" class="expense-review">
        <label for="expense-comment">审批意见（驳回时必填）</label>
        <textarea id="expense-comment" v-model="comment" :disabled="busy" maxlength="1000" rows="3" placeholder="填写核对情况或驳回原因"></textarea>
        <div v-if="!decision" class="row-actions"><button class="primary" :disabled="busy" @click="decision = 'approved'">通过</button><button class="secondary" :disabled="busy" @click="decision = 'rejected'">驳回</button></div>
        <div v-else class="notice"><p>确认{{ decision === 'approved' ? '通过' : '驳回' }} #{{ selected.id }}（¥{{ selected.amount }}）？审批结果提交后不可修改。</p>
          <div class="row-actions"><button class="primary" :disabled="busy || (decision === 'rejected' && !comment.trim())" @click="submitDecision">{{ busy ? '提交中…' : '确认提交审批' }}</button><button class="secondary" :disabled="busy" @click="decision = null">返回修改</button></div>
        </div>
      </div>
    </section>
    <p class="page-footnote">普通员工逐级审批至部门经理；部门经理及以上由直属上级审批。组织关系变化时需重新匹配，原审批记录保留。</p>
  </section>
</template>
