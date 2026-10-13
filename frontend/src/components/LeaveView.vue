<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { RefreshCw } from "@lucide/vue";
import { api, ApiError, displayTime, errorText } from "../api";
import type { User } from "../types";
import type { CalendarOverride, Leave, LeaveBalance, LeavePeriod, LeavePreview, LeaveRequest } from "../leaveTypes";

const props = defineProps<{ user: User; initialId?: number }>();
type View = "mine" | "inbox" | "reviewed" | "all";
type Action = "approved" | "rejected" | "cancel" | "reassign";
interface BalanceSubmission { username: string; days: number; request_key: string; }
const view = ref<View>("mine");
const items = ref<Leave[]>([]), selected = ref<Leave | null>(null);
const detailElement = ref<HTMLElement | null>(null);
const balance = ref<LeaveBalance | null>(null);
const loading = ref(false), busy = ref(false), error = ref(""), notice = ref("");
const page = ref(0), hasMore = ref(false), action = ref<Action | null>(null), comment = ref("");
const showRequest = ref(false), preview = ref<LeavePreview | null>(null), requestKey = ref("");
const uncertain = ref(false), formError = ref("");
const form = reactive<LeaveRequest>({ start_date: "", start_period: "am", end_date: "", end_period: "pm", reason: "" });
const labels: Record<string, string> = { pending: "待审批", waiting: "待前级处理", approved: "已通过", rejected: "已驳回", cancelled: "已撤回", superseded: "已结束 / 已重新分配" };
const canMaintain = computed((): boolean => props.user.permissions.includes("leave:write_all"));
const canCreate = computed((): boolean => props.user.permissions.includes("leave:create"));
const calendarStart = ref(""), calendarEnd = ref(""), overrides = ref<CalendarOverride[]>([]);
const calendarLoaded = ref(false), calendarBusy = ref(false), calendarError = ref(""), calendarNotice = ref("");
const calendarForm = reactive<CalendarOverride>({ date: "", is_workday: false, note: "" });
const balanceUsername = ref(""), balanceDays = ref(""), balanceBusy = ref(false), balanceError = ref(""), balanceNotice = ref("");
const balanceRequestKey = ref(""), balanceUncertain = ref(false), balanceExpanded = ref(false);
const confirmBalance = ref(false), confirmCalendar = ref(false);

watch(form, (): void => { preview.value = null; requestKey.value = ""; formError.value = ""; });
watch(calendarForm, (): void => { confirmCalendar.value = false; });
watch([balanceUsername, balanceDays], (): void => {
  if (!balanceUncertain.value) { confirmBalance.value = false; balanceRequestKey.value = ""; }
});

function period(value: LeavePeriod): string { return value === "am" ? "上午" : "下午"; }
function dateRange(item: LeaveRequest): string {
  return `${item.start_date} ${period(item.start_period)} 至 ${item.end_date} ${period(item.end_period)}`;
}
function daysText(value: number | null | undefined): string { return value == null ? "未配置" : `${value} 天`; }
function pendingStorageKey(): string { return `employee-assistant:leave-pending:${props.user.username}`; }
function clearPendingSubmission(): boolean {
  try { sessionStorage.removeItem(pendingStorageKey()); return true; }
  catch { return false; }
}
function validStoredPreview(value: unknown): value is LeavePreview {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  const datePattern = /^\d{4}-\d{2}-\d{2}$/;
  return typeof item.start_date === "string" && datePattern.test(item.start_date)
    && typeof item.end_date === "string" && datePattern.test(item.end_date)
    && (item.start_period === "am" || item.start_period === "pm")
    && (item.end_period === "am" || item.end_period === "pm")
    && typeof item.reason === "string" && item.reason.trim().length > 0 && item.reason.length <= 1000
    && typeof item.days === "number" && Number.isFinite(item.days) && item.days > 0
    && typeof item.calendar_signature === "string" && /^[a-f0-9]{64}$/.test(item.calendar_signature)
    && Array.isArray(item.work_dates) && item.work_dates.every((date: unknown): boolean => typeof date === "string" && datePattern.test(date))
    && typeof item.calendar_note === "string" && typeof item.routing_issue === "string"
    && (item.current_approver === null || typeof item.current_approver === "string")
    && (item.available_days === null || (typeof item.available_days === "number" && Number.isFinite(item.available_days)));
}
async function restorePendingSubmission(): Promise<void> {
  let saved: string | null;
  try { saved = sessionStorage.getItem(pendingStorageKey()); }
  catch { formError.value = "浏览器无法读取提交记录；再次提交前请先查看“我的请假”，确认是否已有申请。"; showRequest.value = true; return; }
  if (!saved) return;
  try {
    const stored: unknown = JSON.parse(saved);
    if (!stored || typeof stored !== "object" || Array.isArray(stored)) throw new Error("invalid record");
    const record = stored as Record<string, unknown>;
    if (!validStoredPreview(record.snapshot) || typeof record.request_key !== "string" || !/^[a-zA-Z0-9_-]{16,64}$/.test(record.request_key)) throw new Error("invalid record");
    const snapshot = record.snapshot;
    uncertain.value = true;
    showRequest.value = true;
    Object.assign(form, { start_date: snapshot.start_date, start_period: snapshot.start_period, end_date: snapshot.end_date, end_period: snapshot.end_period, reason: snapshot.reason });
    await nextTick();
    preview.value = snapshot;
    requestKey.value = record.request_key;
    formError.value = "已恢复本账号上次提交的年假申请。请重试原申请以确认结果；系统使用同一编号避免重复提交。";
  } catch {
    clearPendingSubmission();
    showRequest.value = true;
    formError.value = "本地提交记录损坏，已停止恢复。请先查看“我的请假”确认是否已提交，再重新填写申请。";
  }
}
function balanceStorageKey(): string { return `employee-assistant:leave-balance-pending:${props.user.username}`; }
function clearPendingBalance(): boolean {
  try { sessionStorage.removeItem(balanceStorageKey()); return true; }
  catch { return false; }
}
function validBalanceSubmission(value: unknown): value is BalanceSubmission {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  return typeof item.username === "string" && item.username.trim().length > 0 && item.username.length <= 100
    && typeof item.days === "number" && Number.isFinite(item.days) && item.days >= 0 && item.days <= 10000 && Number.isInteger(item.days * 2)
    && typeof item.request_key === "string" && /^[a-zA-Z0-9_-]{16,64}$/.test(item.request_key);
}
async function restorePendingBalance(): Promise<void> {
  let saved: string | null;
  try { saved = sessionStorage.getItem(balanceStorageKey()); }
  catch { balanceError.value = "浏览器无法读取余额设置记录；重新设置前请先核对该员工的实际余额。"; balanceExpanded.value = true; return; }
  if (!saved) return;
  balanceExpanded.value = true;
  try {
    const stored: unknown = JSON.parse(saved);
    if (!validBalanceSubmission(stored)) throw new Error("invalid balance record");
    balanceUncertain.value = true;
    balanceUsername.value = stored.username;
    balanceDays.value = String(stored.days);
    await nextTick();
    balanceRequestKey.value = stored.request_key;
    confirmBalance.value = true;
    balanceError.value = "已恢复本账号上次余额设置。请重试原请求确认结果，确认前不能修改员工或天数。";
  } catch {
    clearPendingBalance();
    balanceError.value = "本地余额设置记录损坏，已停止恢复；重新设置前请先核对该员工的实际余额。";
  }
}
async function loadList(): Promise<void> {
  const rows = await api<Leave[]>(`/leave?view=${view.value}&limit=21&offset=${page.value * 20}`);
  hasMore.value = rows.length > 20;
  items.value = rows.slice(0, 20);
}
async function refresh(): Promise<void> {
  const selectedId = selected.value?.id;
  selected.value = null;
  items.value = [];
  hasMore.value = false;
  comment.value = "";
  loading.value = true;
  error.value = "";
  action.value = null;
  try {
    await loadList();
    balance.value = await api<LeaveBalance>("/leave/balance");
    if (selectedId !== undefined) selected.value = await api<Leave>(`/leave/${selectedId}`);
  } catch (err) {
    if (err instanceof ApiError && (err.status === 403 || err.status === 404)) {
      items.value = [];
      hasMore.value = false;
      error.value = "当前列表或请假申请已不存在或当前账号无权访问，请重新选择列表。";
    } else error.value = errorText(err);
  }
  finally { loading.value = false; }
}
async function changeView(value: View): Promise<void> {
  view.value = value;
  page.value = 0;
  selected.value = null;
  notice.value = "";
  await refresh();
}
async function paginate(delta: number): Promise<void> {
  page.value += delta;
  selected.value = null;
  await refresh();
}
async function open(item: Pick<Leave, "id">): Promise<void> {
  busy.value = true;
  error.value = "";
  action.value = null;
  comment.value = "";
  selected.value = null;
  try { selected.value = await api<Leave>(`/leave/${item.id}`); }
  catch (err) {
    if (err instanceof ApiError && (err.status === 403 || err.status === 404)) {
      items.value = [];
      error.value = "该请假申请已不存在或当前账号无权访问，请刷新列表。";
    } else error.value = errorText(err);
  }
  finally { busy.value = false; }
}
async function makePreview(): Promise<void> {
  if (busy.value || uncertain.value) return;
  busy.value = true;
  formError.value = "";
  preview.value = null;
  try {
    preview.value = await api<LeavePreview>("/leave/preview", { method: "POST", body: JSON.stringify(form) });
    requestKey.value = crypto.randomUUID();
  } catch (err) { formError.value = errorText(err); }
  finally { busy.value = false; }
}
async function submitRequest(): Promise<void> {
  if (!preview.value || !requestKey.value || busy.value) return;
  busy.value = true;
  formError.value = "";
  const snapshot = preview.value;
  try {
    sessionStorage.setItem(pendingStorageKey(), JSON.stringify({ snapshot, request_key: requestKey.value }));
  } catch {
    formError.value = "浏览器无法保存提交记录，本次尚未发送申请。请允许此站点使用会话存储后重试。";
    busy.value = false;
    return;
  }
  try {
    const result = await api<Leave>("/leave", {
      method: "POST", body: JSON.stringify({
        start_date: snapshot.start_date, start_period: snapshot.start_period,
        end_date: snapshot.end_date, end_period: snapshot.end_period, reason: snapshot.reason,
        expected_calendar_signature: snapshot.calendar_signature, request_key: requestKey.value,
      }),
    });
    const cleared = clearPendingSubmission();
    uncertain.value = false;
    preview.value = null;
    requestKey.value = "";
    showRequest.value = false;
    Object.assign(form, { start_date: "", start_period: "am", end_date: "", end_period: "pm", reason: "" });
    view.value = "mine";
    page.value = 0;
    selected.value = result;
    notice.value = `年假申请 #${result.id} 已提交，已预占 ${result.days} 天额度。`;
    if (!cleared) notice.value += " 浏览器未能清除本地提交记录；再次打开页面时可重试原请求核对同一申请。";
    await refresh();
  } catch (err) {
    uncertain.value = !(err instanceof ApiError) || err.status === 0 || err.status >= 500;
    if (!uncertain.value) { clearPendingSubmission(); preview.value = null; requestKey.value = ""; }
    formError.value = uncertain.value
      ? `${errorText(err)} 尚未确认提交结果，请使用下方按钮重试原申请，避免重复新建。`
      : `${errorText(err)} 请核对填写内容，重新核算天数并预览后再提交。`;
  } finally { busy.value = false; }
}
function prepare(value: Action): void {
  action.value = value;
  notice.value = "";
  error.value = "";
}
async function submitAction(): Promise<void> {
  if (!selected.value || !action.value || busy.value) return;
  const item = selected.value, pending = action.value;
  if (pending === "rejected" && !comment.value.trim()) { error.value = "请填写驳回原因。"; return; }
  let path = `/leave/${item.id}/${pending}`;
  let body: object = { version: item.version };
  if (pending === "approved" || pending === "rejected") {
    const step = item.steps.find((entry): boolean => entry.round === item.approval_round && entry.status === "pending");
    if (!step) { error.value = "审批步骤已变化，请刷新详情。"; return; }
    path = `/leave/${item.id}/review`;
    body = { step_id: step.id, decision: pending, comment: comment.value.trim() };
  }
  busy.value = true;
  error.value = "";
  try {
    selected.value = await api<Leave>(path, { method: "POST", body: JSON.stringify(body) });
    notice.value = pending === "cancel" ? "申请已撤回，预占额度已释放。"
      : pending === "reassign" ? "审批人已重新匹配，将从第一级重新审批，原记录保留。"
      : selected.value.status === "pending" ? "本级审批已通过，已流转至下一级。" : "审批结果已保存。";
    action.value = null;
    comment.value = "";
    await refresh();
  } catch (err) {
    error.value = `${errorText(err)} 请刷新详情后再操作。`;
    action.value = null;
  } finally { busy.value = false; }
}
async function loadCalendar(): Promise<void> {
  if (!calendarStart.value || !calendarEnd.value) { calendarError.value = "请选择查询的起止日期。"; return; }
  calendarBusy.value = true;
  calendarError.value = "";
  calendarLoaded.value = false;
  try {
    overrides.value = await api<CalendarOverride[]>(`/leave/calendar?start_date=${encodeURIComponent(calendarStart.value)}&end_date=${encodeURIComponent(calendarEnd.value)}`);
    calendarLoaded.value = true;
  } catch (err) { calendarError.value = errorText(err); }
  finally { calendarBusy.value = false; }
}
async function saveCalendar(): Promise<void> {
  if (calendarBusy.value || !confirmCalendar.value) return;
  calendarBusy.value = true;
  calendarError.value = "";
  calendarNotice.value = "";
  try {
    await api<void>("/leave/calendar", { method: "POST", body: JSON.stringify(calendarForm) });
    confirmCalendar.value = false;
    calendarNotice.value = `${calendarForm.date} 已设为${calendarForm.is_workday ? "工作日" : "休息日"}。`;
    // A calendar change requires a fresh server preview before any new application.
    if (!uncertain.value) { preview.value = null; requestKey.value = ""; }
    calendarStart.value ||= calendarForm.date;
    calendarEnd.value ||= calendarForm.date;
    await loadCalendar();
    await refresh();
  } catch (err) { calendarError.value = errorText(err); }
  finally { calendarBusy.value = false; }
}
function prepareBalance(): void {
  if (balanceBusy.value || balanceUncertain.value) return;
  balanceError.value = "";
  const value = Number(balanceDays.value);
  if (!balanceUsername.value.trim() || !balanceDays.value.trim() || !Number.isFinite(value) || value < 0 || value > 10000 || !Number.isInteger(value * 2)) {
    balanceError.value = "请填写员工账号和有效的剩余年假天数（0–10000，以半天为单位）。";
    return;
  }
  balanceRequestKey.value = crypto.randomUUID();
  confirmBalance.value = true;
}
function toggleBalance(event: Event): void {
  if (event.target instanceof HTMLDetailsElement) balanceExpanded.value = event.target.open;
}
async function saveBalance(): Promise<void> {
  if (balanceBusy.value || !confirmBalance.value || !balanceRequestKey.value) return;
  const payload: BalanceSubmission = { username: balanceUsername.value.trim(), days: Number(balanceDays.value), request_key: balanceRequestKey.value };
  if (!validBalanceSubmission(payload)) { balanceError.value = "余额设置内容无效，请核对后重新确认。"; return; }
  balanceBusy.value = true;
  balanceError.value = "";
  balanceNotice.value = "";
  try {
    sessionStorage.setItem(balanceStorageKey(), JSON.stringify(payload));
  } catch {
    balanceError.value = "浏览器无法保存余额设置记录，本次尚未发送。请允许此站点使用会话存储后重试。";
    balanceBusy.value = false;
    return;
  }
  const retrying = balanceUncertain.value;
  try {
    await api<void>("/leave/balance", { method: "POST", body: JSON.stringify(payload) });
    const cleared = clearPendingBalance();
    balanceUncertain.value = false;
    balanceRequestKey.value = "";
    balanceNotice.value = retrying
      ? `已确认账号 ${payload.username} 的原余额设置请求成功；重试不会再次覆盖余额，此后审批可能已扣减余额。`
      : `账号 ${payload.username} 的剩余年假已设为 ${payload.days} 天。`;
    if (!cleared) balanceNotice.value += " 浏览器未能清除本地记录，再次打开时可重试原请求核对。";
    confirmBalance.value = false;
    await refresh();
  } catch (err) {
    balanceUncertain.value = !(err instanceof ApiError) || err.status === 0 || err.status >= 500;
    if (!balanceUncertain.value) { clearPendingBalance(); balanceRequestKey.value = ""; confirmBalance.value = false; }
    balanceError.value = balanceUncertain.value
      ? `${errorText(err)} 尚未确认设置结果，请重试原请求；确认前不能更改员工和天数。`
      : `${errorText(err)} 请核对员工和天数后重新确认。`;
  }
  finally { balanceBusy.value = false; }
}
onMounted(async (): Promise<void> => {
  await restorePendingSubmission();
  await restorePendingBalance();
  await refresh();
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
      <div><h1>请假与审批</h1><p class="muted">提交年假申请，跟进审批进度，查看余额与处理记录。</p></div>
      <button class="secondary" :disabled="loading || busy" @click="refresh"><RefreshCw :size="16" />刷新</button>
    </div>
    <div class="leave-balance" aria-label="我的年假余额">
      <span>剩余年假 <strong>{{ daysText(balance?.balance_days) }}</strong></span>
      <span>待审批预占 <strong>{{ daysText(balance?.reserved_days) }}</strong></span>
      <span>可申请 <strong>{{ daysText(balance?.available_days) }}</strong></span>
      <button v-if="canCreate" class="primary" :disabled="busy" @click="showRequest = !showRequest">{{ showRequest ? '收起申请' : '申请年假' }}</button>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <section v-if="showRequest && canCreate" class="leave-request" aria-label="申请年假">
      <h3>申请年假</h3>
      <p class="muted">按工作日历计算，起止时段均计入请假范围。默认周一至周五为工作日；节假日和调休由人事维护。</p>
      <form @submit.prevent="makePreview">
        <fieldset class="leave-fields" :disabled="busy || uncertain">
          <div class="leave-date-row"><label for="leave-start">开始日期</label><input id="leave-start" v-model="form.start_date" type="date" required /><select v-model="form.start_period" aria-label="开始时段"><option value="am">上午</option><option value="pm">下午</option></select></div>
          <div class="leave-date-row"><label for="leave-end">结束日期</label><input id="leave-end" v-model="form.end_date" type="date" required /><select v-model="form.end_period" aria-label="结束时段"><option value="am">上午</option><option value="pm">下午</option></select></div>
          <label for="leave-reason">请假事由</label><textarea id="leave-reason" v-model="form.reason" rows="3" required maxlength="1000" placeholder="填写请假事由"></textarea>
          <button class="secondary" type="submit">{{ busy ? '正在核算…' : '核算天数并预览' }}</button>
        </fieldset>
      </form>
      <p v-if="formError" class="notice error" role="alert">{{ formError }}</p>
      <div v-if="preview" class="notice leave-preview" aria-label="请假提交确认">
        <h3>请核对申请</h3>
        <p>{{ user.username }} · 年假 <strong>{{ preview.days }} 天</strong></p>
        <p>{{ dateRange(preview) }}（含起止时段）</p>
        <p class="expense-description">事由：{{ preview.reason }}</p>
        <p>当前可申请：{{ daysText(preview.available_days) }} · 首位审批人：{{ preview.current_approver || '尚未配置' }}</p>
        <p v-if="preview.routing_issue">{{ preview.routing_issue }}</p>
        <p class="muted">{{ preview.calendar_note }}</p>
        <details><summary>查看计入的工作日</summary><p class="expense-description">{{ preview.work_dates.join('、') }}</p></details>
        <p>确认提交后预占额度，最终审批通过后扣减；驳回或撤回将释放预占额度。</p>
        <button class="primary" :disabled="busy" @click="submitRequest">{{ busy ? '提交中…' : uncertain ? '重试并确认原申请结果' : '确认提交年假申请' }}</button>
      </div>
    </section>
    <div class="row-actions knowledge-tabs" aria-label="请假列表范围">
      <button v-for="tab in ([['mine', '我的请假'], ['inbox', '待我审批'], ['reviewed', '我已审批']] as const)" :key="tab[0]" :class="view === tab[0] ? 'primary' : 'secondary'" :disabled="loading || busy" @click="changeView(tab[0])">{{ tab[1] }}</button>
      <button v-if="user.permissions.includes('leave:read_all')" :class="view === 'all' ? 'primary' : 'secondary'" :disabled="loading || busy" @click="changeView('all')">全部请假</button>
    </div>
    <p v-if="loading" class="empty-state">正在读取申请…</p>
    <div v-else-if="!items.length" class="empty-state table-card"><h3>当前没有请假申请</h3><p>可在本页申请年假，也可以向工作助手说明请假日期和事由。</p></div>
    <div v-else class="table-card table-scroll">
      <table><thead><tr><th>编号 / 申请人</th><th>时间 / 天数</th><th>状态 / 当前审批人</th><th>操作</th></tr></thead>
        <tbody><tr v-for="item in items" :key="item.id">
          <td>#{{ item.id }}<small class="cell-detail">{{ item.created_by }}</small></td>
          <td>{{ item.start_date }} {{ period(item.start_period) }}<small class="cell-detail">至 {{ item.end_date }} {{ period(item.end_period) }} · {{ item.days }} 天</small></td>
          <td>{{ item.calendar_issue ? '工作日历已变化' : item.routing_issue ? '待配置审批人' : labels[item.status] }}<small class="cell-detail">{{ item.current_approver || '—' }}</small></td>
          <td><button class="secondary" :disabled="busy" @click="open(item)">{{ item.can_review ? '查看并审批' : '查看详情' }}</button></td>
        </tr></tbody>
      </table>
    </div>
    <div class="row-actions expense-pagination"><button class="secondary" :disabled="page === 0 || loading || busy" @click="paginate(-1)">上一页</button><span>第 {{ page + 1 }} 页</span><button class="secondary" :disabled="!hasMore || loading || busy" @click="paginate(1)">下一页</button></div>
    <section v-if="selected" ref="detailElement" class="expense-detail" aria-label="请假详情">
      <div class="section-title"><h3>年假 #{{ selected.id }}</h3><button class="text-button" :disabled="busy" @click="selected = null; action = null">关闭详情</button></div>
      <p>{{ selected.created_by }} · <strong>{{ selected.days }} 天</strong> · {{ labels[selected.status] }}</p>
      <p>{{ dateRange(selected) }}（含起止时段）</p>
      <p class="expense-description">事由：{{ selected.reason }}</p>
      <p class="muted">提交于 {{ displayTime(selected.created_at) }}</p>
      <p v-if="selected.calendar_issue" class="notice error">{{ selected.calendar_issue }} 请撤回后按新工作日历重新申请。</p>
      <p v-if="selected.routing_issue" class="notice">{{ selected.routing_issue }}</p>
      <h4>审批记录</h4>
      <ol v-if="selected.steps.length" class="expense-history"><li v-for="step in selected.steps" :key="step.id"><strong>第 {{ step.round }} 轮 · 第 {{ step.position }} 级 · {{ step.approver }}</strong> — {{ labels[step.status] }}<small v-if="step.reviewed_at" class="cell-detail">{{ displayTime(step.reviewed_at) }}</small><p v-if="step.comment" class="expense-description">{{ step.comment }}</p></li></ol>
      <p v-else class="muted">尚未分配审批人，请补全组织关系后重新匹配。</p>
      <div v-if="selected.can_review" class="expense-review"><label for="leave-comment">审批意见（驳回时必填）</label><textarea id="leave-comment" v-model="comment" :disabled="busy" maxlength="1000" rows="3" placeholder="填写核对情况或驳回原因"></textarea></div>
      <div v-if="!action" class="row-actions leave-actions">
        <button v-if="selected.can_review" class="primary" :disabled="busy" @click="prepare('approved')">通过</button>
        <button v-if="selected.can_review" class="secondary" :disabled="busy" @click="prepare('rejected')">驳回</button>
        <button v-if="selected.can_cancel" class="secondary" :disabled="busy" @click="prepare('cancel')">撤回申请</button>
        <button v-if="selected.can_reassign" class="secondary" :disabled="busy" @click="prepare('reassign')">按当前组织关系重新匹配</button>
      </div>
      <div v-else class="notice"><p>确认{{ action === 'cancel' ? '撤回' : action === 'reassign' ? '重新匹配审批人' : action === 'approved' ? '通过' : '驳回' }}年假申请 #{{ selected.id }}（{{ selected.days }} 天）？{{ action === 'cancel' ? '撤回后释放预占额度。' : action === 'reassign' ? '将从第一级重新审批，原记录保留。' : '本次审批结果提交后不可修改。' }}</p>
        <div class="row-actions"><button class="primary" :disabled="busy || (action === 'rejected' && !comment.trim())" @click="submitAction">{{ busy ? '提交中…' : '确认提交' }}</button><button class="secondary" :disabled="busy" @click="action = null">返回</button></div>
      </div>
    </section>
    <details v-if="canMaintain" class="leave-maintenance"><summary>工作日历维护</summary>
      <p class="muted">默认周一至周五工作，周末休息；默认日历不含法定节假日和调休。请按公司安排配置覆盖日期。修改涉及待审批申请时，该申请须由员工撤回重提。</p>
      <p v-if="calendarError" class="notice error" role="alert">{{ calendarError }}</p><p v-if="calendarNotice" class="notice" role="status">{{ calendarNotice }}</p>
      <form class="leave-calendar-query" @submit.prevent="loadCalendar"><label for="calendar-start">查询工作日历</label><input id="calendar-start" v-model="calendarStart" type="date" :disabled="calendarBusy" required /><span>至</span><input v-model="calendarEnd" type="date" aria-label="日历查询结束日期" :disabled="calendarBusy" required /><button class="secondary" :disabled="calendarBusy">查询</button><small class="muted">最多 366 天，显示该区间每日最终安排</small></form>
      <div v-if="calendarLoaded && overrides.length" class="table-card table-scroll"><table><thead><tr><th>日期</th><th>安排</th><th>说明</th></tr></thead><tbody><tr v-for="item in overrides" :key="item.date"><td>{{ item.date }}</td><td>{{ item.is_workday ? '工作日' : '休息日' }}</td><td class="expense-description">{{ item.note || '—' }}</td></tr></tbody></table></div>
      <p v-else-if="calendarLoaded" class="muted">该区间没有返回日历记录，请核对查询日期。</p>
      <form class="leave-fields" @submit.prevent="confirmCalendar = true">
        <h4>配置单个日期</h4>
        <div class="leave-date-row"><label for="calendar-date">日期</label><input id="calendar-date" v-model="calendarForm.date" type="date" :disabled="calendarBusy" required /><select v-model="calendarForm.is_workday" aria-label="工作日或休息日" :disabled="calendarBusy"><option :value="true">工作日</option><option :value="false">休息日</option></select></div>
        <label for="calendar-note">安排说明</label><input id="calendar-note" v-model="calendarForm.note" :disabled="calendarBusy" maxlength="200" placeholder="如：公司调休、节假日休息" />
        <button class="secondary" :disabled="calendarBusy">保存日期安排</button>
      </form>
      <div v-if="confirmCalendar" class="notice"><p>确认将 {{ calendarForm.date }} 设为{{ calendarForm.is_workday ? '工作日' : '休息日' }}？受影响的待审批申请需要撤回重提。</p><div class="row-actions"><button class="primary" :disabled="calendarBusy" @click="saveCalendar">确认保存</button><button class="secondary" :disabled="calendarBusy" @click="confirmCalendar = false">取消</button></div></div>
    </details>
    <details v-if="canMaintain" class="leave-maintenance" :open="balanceExpanded" @toggle="toggleBalance"><summary>员工年假余额维护</summary>
      <p class="muted">设置该员工尚未扣减的剩余年假，包含待审批申请预占的额度；可申请额度由系统扣除预占后计算。此处填写新余额，不是增加天数。</p>
      <p v-if="balanceError" class="notice error" role="alert">{{ balanceError }}</p><p v-if="balanceNotice" class="notice" role="status">{{ balanceNotice }}</p>
      <form class="leave-fields" @submit.prevent="prepareBalance"><label for="leave-balance-user">员工账号</label><input id="leave-balance-user" v-model="balanceUsername" :disabled="balanceBusy || balanceUncertain" required maxlength="100" autocomplete="off" /><label for="leave-balance-days">剩余年假（天）</label><input id="leave-balance-days" v-model="balanceDays" type="number" min="0" max="10000" step="0.5" :disabled="balanceBusy || balanceUncertain" required /><button class="secondary" :disabled="balanceBusy || balanceUncertain">设置余额</button></form>
      <div v-if="confirmBalance" class="notice"><p>{{ balanceUncertain ? '待确认的原请求：' : '确认将' }} {{ balanceUsername }} 的剩余年假设为 {{ balanceDays }} 天{{ balanceUncertain ? '。重试只确认原请求，不会重复覆盖余额。' : '？' }}</p><div class="row-actions"><button class="primary" :disabled="balanceBusy" @click="saveBalance">{{ balanceBusy ? '提交中…' : balanceUncertain ? '重试原余额设置' : '确认设置' }}</button><button v-if="!balanceUncertain" class="secondary" :disabled="balanceBusy" @click="confirmBalance = false; balanceRequestKey = ''">取消</button></div></div>
    </details>
    <p class="page-footnote">第一版支持年假。普通员工逐级审批至部门经理；部门经理及以上由直属上级审批。待审批申请可由本人撤回。</p>
  </section>
</template>

<style scoped>
.leave-balance { display: flex; flex-wrap: wrap; align-items: center; gap: 16px 24px; margin-bottom: 24px; padding-bottom: 18px; border-bottom: 1px solid var(--line); }
.leave-balance strong { margin-left: 6px; font-variant-numeric: tabular-nums; }
.leave-request { margin: 24px 0; padding: 20px; border: 1px solid var(--line); border-radius: 5px; }
.leave-fields { display: grid; gap: 12px; min-width: 0; max-width: 660px; border: 0; padding: 0; margin: 16px 0; }
.leave-fields textarea { width: 100%; padding: 12px; resize: vertical; border: 1px solid var(--line); border-radius: 4px; font: inherit; }
.leave-fields > button { justify-self: start; }
.leave-date-row { display: grid; align-items: center; grid-template-columns: 80px minmax(140px, 1fr) 90px; gap: 12px; }
.leave-preview { margin-bottom: 0; }
.leave-preview details { margin-bottom: 16px; }
.leave-preview summary, .leave-maintenance summary { cursor: pointer; }
.leave-preview details p { margin-top: 12px; }
.leave-actions { margin-top: 20px; }
.leave-maintenance { padding: 20px 0; border-top: 1px solid var(--line); }
.leave-maintenance > summary { font-weight: 600; }
.leave-maintenance > p { margin-top: 16px; }
.leave-calendar-query { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 16px 0; }
@media (max-width: 600px) {
  .leave-request { padding: 16px; }
  .leave-date-row { grid-template-columns: minmax(120px, 1fr) 86px; gap: 8px; }
  .leave-date-row label { grid-column: 1 / -1; }
}
</style>
