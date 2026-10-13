<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import {
  BarChart3,
  ListTodo,
  Receipt,
  CalendarDays,
  Wrench,
  BookOpen,
  ChevronRight,
  Headphones,
  Building2,
  LogOut,
  Menu,
  MessageSquare,
  Plus,
  ShieldCheck,
  Users,
  X,
} from "@lucide/vue";
import { api, errorText } from "./api";
import {
  busy,
  chatError,
  conversations,
  hasMoreConversations,
  historyLoading,
  loadMoreConversations,
  newConversation,
  refreshConversations,
  resetChat,
  selectConversation,
  selectedId,
} from "./chat";
import LoginView from "./components/LoginView.vue";
import ChatView from "./components/ChatView.vue";
import KnowledgeView from "./components/KnowledgeView.vue";
import TicketsView from "./components/TicketsView.vue";
import LeaveView from "./components/LeaveView.vue";
import ExpensesView from "./components/ExpensesView.vue";
import HandoffView from "./components/HandoffView.vue";
import InsightsView from "./components/InsightsView.vue";
import UsersView from "./components/UsersView.vue";
import WorkCenterView from "./components/WorkCenterView.vue";
import type { User } from "./types";
import type { WorkKind, WorkSummary, WorkTarget } from "./workCenterTypes";

type View =
  "chat" | "work-center" | "tickets" | "leave" | "expenses" | "knowledge" | "handoffs" | "insights" | "users" | "profile";
const user = ref<User | null>(null),
  ready = ref(false),
  pageError = ref(""),
  sidebarOpen = ref(false);
const view = ref<View>("chat");
const workSummary = ref<WorkSummary>({ todo_count: 0, unread_count: 0 });
const targetId = ref<number | undefined>();
let summaryVersion = 0;
let summaryTimer: ReturnType<typeof setInterval> | undefined;
async function refreshSummary(): Promise<void> {
  if (!user.value || document.hidden) return;
  const version = ++summaryVersion;
  const username = user.value.username;
  try {
    const result = await api<WorkSummary>("/work-center/summary");
    if (version === summaryVersion && user.value?.username === username) workSummary.value = result;
  } catch { /* 角标请求失败时保留当前表单，下一次刷新重试。 */ }
}
const names: Record<View, string> = {
  chat: "工作助手",
  "work-center": "待办与通知",
  tickets: "IT 工单",
  leave: "请假与审批",
  expenses: "报销与审批",
  knowledge: "知识库",
  handoffs: "人工协助",
  insights: "反馈与统计",
  profile: "我的权限",
  users: "员工与组织",
};
const roles: Record<string, string> = {
  employee: "员工",
  admin: "管理员",
  hr: "人事",
  it: "IT",
  finance: "财务",
};
const permissionNames: Record<string, string> = {
  "kb:general": "阅读全员知识",
  "kb:hr": "阅读人事知识",
  "kb:it": "阅读 IT 知识",
  "kb:finance": "阅读财务知识",
  "kb:write": "管理知识库",
  "user:create": "管理员工组织与账号",
  "leave:create": "提交年假申请",
  "leave:read_self": "查询本人请假余额",
  "leave:read_all": "读取全部请假余额",
  "leave:write_all": "维护请假余额",
  "ticket:create": "创建 IT 工单",
  "ticket:manage": "处理 IT 工单",
  "ticket:read_all": "读取全部 IT 工单",
  "expense:create": "发起报销",
  "expense:read_all": "读取全部报销",
  "meeting:read": "查询会议室",
  "meeting:book": "预订会议室",
  "meeting:write": "维护会议室",
  "handoff:manage": "处理人工待办",
};
const navigation = computed(() =>
  [
    {
      id: "users" as View,
      label: "员工与组织",
      icon: Users,
      visible: user.value?.permissions.includes("user:create"),
    },
    {
      id: "chat" as View,
      label: "工作助手",
      icon: MessageSquare,
      visible: true,
    },
    { id: "work-center" as View, label: "待办与通知", icon: ListTodo, visible: true },
    { id: "tickets" as View, label: "IT 工单", icon: Wrench, visible: true },
    { id: "leave" as View, label: "请假与审批", icon: CalendarDays, visible: true },
    { id: "expenses" as View, label: "报销与审批", icon: Receipt, visible: true },
    { id: "knowledge" as View, label: "知识库", icon: BookOpen, visible: true },
    {
      id: "handoffs" as View,
      label: "人工协助",
      icon: Headphones,
      visible: true,
    },
    {
      id: "insights" as View,
      label: "反馈与统计",
      icon: BarChart3,
      visible: user.value?.permissions.includes("kb:write"),
    },
  ]
    .filter((item) => item.visible)
    .sort(
      (a, b) =>
        ["chat", "work-center", "tickets", "leave", "expenses", "knowledge", "handoffs", "users", "insights"].indexOf(a.id) -
        ["chat", "work-center", "tickets", "leave", "expenses", "knowledge", "handoffs", "users", "insights"].indexOf(b.id),
    ),
);
function navigate(target: View): void {
  view.value = target;
  targetId.value = undefined;
  void refreshSummary();
  sidebarOpen.value = false;
}
function openWorkItem(target: WorkTarget): void {
  const destinations: Record<WorkKind, View> = { leave: "leave", expense: "expenses", ticket: "tickets", handoff: "handoffs" };
  navigate(destinations[target.kind]);
  targetId.value = target.record_id;
}
async function loggedIn(profile: User): Promise<void> {
  summaryVersion++;
  user.value = profile;
  view.value = "chat";
  pageError.value = "";
  resetChat();
  targetId.value = undefined;
  workSummary.value = { todo_count: 0, unread_count: 0 };
  void refreshSummary();
  try {
    await refreshConversations();
    const latest = conversations.value[0];
    if (latest) await selectConversation(latest.id);
  } catch (error) {
    chatError.value = errorText(error);
  }
}
async function logout(): Promise<void> {
  try {
    await api<void>("/auth/logout", { method: "POST" });
    expire();
  } catch (error) {
    pageError.value = errorText(error);
  }
}
function expire(): void {
  summaryVersion++;
  workSummary.value = { todo_count: 0, unread_count: 0 };
  targetId.value = undefined;
  sidebarOpen.value = false;
  user.value = null;
  resetChat();
}
async function startNew(): Promise<void> {
  await newConversation();
  navigate("chat");
}
async function choose(id: string): Promise<void> {
  navigate("chat");
  await selectConversation(id);
}
async function refreshProfile(): Promise<void> {
  try {
    user.value = await api<User>("/auth/me");
    void refreshSummary();
  } catch (err) {
    pageError.value = errorText(err);
  }
}
const levelNames: Record<string, string> = {
  employee: "普通员工",
  team_lead: "组长",
  department_manager: "部门经理",
  general_manager: "总经理",
  chairman: "董事长",
};
onMounted(async (): Promise<void> => {
  window.addEventListener("session-expired", expire);
  window.addEventListener("focus", refreshSummary);
  document.addEventListener("visibilitychange", refreshSummary);
  summaryTimer = setInterval(refreshSummary, 30000);
  try {
    await loggedIn(await api<User>("/auth/me"));
  } catch {
    /* 未登录时显示登录页 */
  } finally {
    ready.value = true;
  }
});
onUnmounted((): void => {
  window.removeEventListener("session-expired", expire);
  window.removeEventListener("focus", refreshSummary);
  document.removeEventListener("visibilitychange", refreshSummary);
  if (summaryTimer) clearInterval(summaryTimer);
});
</script>

<template>
  <div v-if="!ready" class="boot-screen">
    <Building2 :size="30" />
    <p>正在加载…</p>
  </div>
  <LoginView v-else-if="!user" @login="loggedIn" />
  <div v-else class="app-shell">
    <div
      v-if="sidebarOpen"
      class="sidebar-backdrop"
      @click="sidebarOpen = false"
    ></div>
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <div class="brand">
        <span class="brand-mark"><Building2 :size="23" /></span
        ><strong>栖知<span>员工工作台</span></strong
        ><button
          class="icon-button mobile-only"
          aria-label="关闭导航"
          @click="sidebarOpen = false"
        >
          <X :size="18" />
        </button>
      </div>
      <button class="new-chat" :disabled="busy" @click="startNew">
        <Plus :size="18" />新建对话
      </button>
      <span class="sidebar-label">应用</span>
      <nav aria-label="主导航">
        <button
          v-for="item in navigation"
          :key="item.id"
          :class="{ active: view === item.id }"
          @click="navigate(item.id)"
        >
          <component :is="item.icon" :size="18" /><span>{{ item.label }}<small v-if="item.id === 'work-center'" class="work-center-counts" :aria-label="`${workSummary.todo_count} 项待办，${workSummary.unread_count} 条未读通知`">待办 {{ workSummary.todo_count }} · 未读 {{ workSummary.unread_count }}</small></span><ChevronRight v-if="view === item.id" :size="14" />
        </button>
      </nav>
      <div class="history-section">
        <span class="sidebar-label"
          >最近对话 <span>{{ conversations.length }}</span></span
        >
        <div class="history-list">
          <button
            v-for="item in conversations"
            :key="item.id"
            :disabled="busy"
            :class="{ selected: selectedId === item.id && view === 'chat' }"
            @click="choose(item.id)"
          >
            <MessageSquare :size="14" /><span>{{ item.title }}</span>
          </button>
          <p v-if="!conversations.length" class="history-empty">
            你的对话会保存在这里
          </p>
          <button
            v-if="hasMoreConversations"
            :disabled="historyLoading || busy"
            @click="loadMoreConversations"
          >
            {{ historyLoading ? "加载中…" : "加载更早的对话" }}
          </button>
        </div>
      </div>
      <div class="sidebar-bottom">
        <button class="account" @click="navigate('profile')">
          <span class="avatar">{{
            user.username.slice(0, 1).toUpperCase()
          }}</span
          ><span
            ><strong>{{ user.username }}</strong
            ><small>{{
              user.department_name || roles[user.role] || user.role
            }}</small></span
          ><ShieldCheck :size="17" />
        </button>
      </div>
    </aside>
    <main class="main-panel" :class="{ 'is-chat': view === 'chat' }">
      <header class="topbar">
        <div class="breadcrumb">
          <button
            class="icon-button mobile-only"
            aria-label="打开导航"
            @click="sidebarOpen = true"
          >
            <Menu :size="21" /></button
          ><span>员工工作台</span><ChevronRight :size="14" /><strong>{{
            names[view]
          }}</strong>
        </div>
        <div class="topbar-actions">
          <span class="privacy-label"
            ><ShieldCheck :size="14" />{{ roles[user.role] || user.role }}</span
          ><button
            class="icon-button"
            title="退出登录"
            aria-label="退出登录"
            :disabled="busy"
            @click="logout"
          >
            <LogOut :size="18" />
          </button>
        </div>
      </header>
      <p v-if="pageError" class="notice error" role="alert">{{ pageError }}</p>
      <ChatView v-if="view === 'chat'" :user="user" />
      <WorkCenterView v-else-if="view === 'work-center'" :key="user.username" @open="openWorkItem" @changed="refreshSummary" />
      <KnowledgeView v-else-if="view === 'knowledge'" :user="user" />
      <TicketsView v-else-if="view === 'tickets'" :user="user" :initial-id="targetId" />
      <LeaveView v-else-if="view === 'leave'" :user="user" :initial-id="targetId" />
      <ExpensesView v-else-if="view === 'expenses'" :user="user" :initial-id="targetId" />
      <HandoffView v-else-if="view === 'handoffs'" :user="user" :initial-id="targetId" @changed="refreshSummary" />
      <InsightsView v-else-if="view === 'insights'" />
      <UsersView v-else-if="view === 'users'" @saved="refreshProfile" />
      <section v-else class="page-content">
        <div class="page-heading">
          <div>
            <h1>账号与权限</h1>
            <p class="muted">
              当前登录为 {{ user.display_name || user.username }}，权限角色为{{
                roles[user.role] || user.role
              }}。
            </p>
            <p class="muted">
              部门：{{ user.department_name || "未分配" }} · 职级：{{
                levelNames[user.job_level]
              }}
              · 直属上级：{{ user.manager_username || "未分配" }}
            </p>
          </div>
          <ShieldCheck :size="32" :stroke-width="1.3" />
        </div>
        <div class="permission-grid">
          <article v-for="permission in user.permissions" :key="permission">
            <ShieldCheck :size="20" />
            <div>
              <strong>{{ permissionNames[permission] || permission }}</strong
              ><small>{{ permission }}</small>
            </div>
          </article>
        </div>
        <p class="page-footnote">
          这些权限来自账号角色配置。如需调整，请联系管理员。
        </p>
      </section>
    </main>
  </div>
</template>

<style scoped>
.work-center-counts { display: block; margin-top: 3px; font-size: 11px; color: inherit; opacity: .8; font-weight: 400; }
</style>
