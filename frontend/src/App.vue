<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import {
  ArrowUpRight,
  BarChart3,
  BookOpen,
  ChevronRight,
  CircleHelp,
  Headphones,
  Layers3,
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
import HandoffView from "./components/HandoffView.vue";
import InsightsView from "./components/InsightsView.vue";
import UsersView from "./components/UsersView.vue";
import type { User } from "./types";

type View =
  "chat" | "knowledge" | "handoffs" | "insights" | "users" | "profile";
const user = ref<User | null>(null),
  ready = ref(false),
  pageError = ref(""),
  sidebarOpen = ref(false);
const view = ref<View>("chat");
const names: Record<View, string> = {
  chat: "智能助手",
  knowledge: "知识库",
  handoffs: "人工待办",
  insights: "反馈与统计",
  profile: "我的权限",
  users: "用户管理",
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
  "user:create": "创建员工账号",
  "leave:read_self": "查询本人请假余额",
  "leave:read_all": "读取全部请假余额",
  "leave:write_all": "维护请假余额",
  "ticket:create": "创建 IT 工单",
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
      label: "用户管理",
      icon: Users,
      visible: user.value?.permissions.includes("user:create"),
    },
    {
      id: "chat" as View,
      label: "智能助手",
      icon: MessageSquare,
      visible: true,
    },
    { id: "knowledge" as View, label: "知识库", icon: BookOpen, visible: true },
    {
      id: "handoffs" as View,
      label: "人工待办",
      icon: Headphones,
      visible: user.value?.permissions.includes("handoff:manage"),
    },
    {
      id: "insights" as View,
      label: "反馈与统计",
      icon: BarChart3,
      visible: user.value?.permissions.includes("kb:write"),
    },
  ].filter((item) => item.visible),
);
function navigate(target: View): void {
  view.value = target;
  sidebarOpen.value = false;
}
async function loggedIn(profile: User): Promise<void> {
  user.value = profile;
  view.value = "chat";
  pageError.value = "";
  resetChat();
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
onMounted(async (): Promise<void> => {
  window.addEventListener("session-expired", expire);
  try {
    await loggedIn(await api<User>("/auth/me"));
  } catch {
    /* 未登录时显示登录页 */
  } finally {
    ready.value = true;
  }
});
onUnmounted(() => window.removeEventListener("session-expired", expire));
</script>

<template>
  <div v-if="!ready" class="boot-screen">
    <Layers3 :size="30" />
    <p>正在打开工作空间…</p>
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
        <span class="brand-mark"><Layers3 :size="23" /></span
        ><strong>栖知<span>WORKSPACE ASSISTANT</span></strong
        ><button
          class="icon-button mobile-only"
          aria-label="关闭导航"
          @click="sidebarOpen = false"
        >
          <X :size="18" />
        </button>
      </div>
      <button class="new-chat" :disabled="busy" @click="startNew">
        <Plus :size="18" />开始新对话<span>↗</span>
      </button>
      <span class="sidebar-label">工作空间</span>
      <nav aria-label="主导航">
        <button
          v-for="item in navigation"
          :key="item.id"
          :class="{ active: view === item.id }"
          @click="navigate(item.id)"
        >
          <component :is="item.icon" :size="18" /><span>{{ item.label }}</span
          ><ChevronRight v-if="view === item.id" :size="14" />
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
        <div class="sidebar-tip">
          <CircleHelp :size="17" />
          <div>
            <strong>让问题找到答案</strong>
            <p>制度有据可查，事务及时处理。</p>
          </div>
        </div>
        <button class="account" @click="navigate('profile')">
          <span class="avatar">{{
            user.username.slice(0, 1).toUpperCase()
          }}</span
          ><span
            ><strong>{{ user.username }}</strong
            ><small>{{ roles[user.role] || user.role }}工作空间</small></span
          ><ShieldCheck :size="17" />
        </button>
      </div>
    </aside>
    <main class="main-panel">
      <header class="topbar">
        <div class="breadcrumb">
          <button
            class="icon-button mobile-only"
            aria-label="打开导航"
            @click="sidebarOpen = true"
          >
            <Menu :size="21" /></button
          ><span>工作空间</span><ChevronRight :size="14" /><strong>{{
            names[view]
          }}</strong>
        </div>
        <div class="topbar-actions">
          <span class="privacy-label"
            ><ShieldCheck :size="14" />按账号权限访问</span
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
      <KnowledgeView v-else-if="view === 'knowledge'" :user="user" />
      <HandoffView v-else-if="view === 'handoffs'" />
      <InsightsView v-else-if="view === 'insights'" />
      <UsersView v-else-if="view === 'users'" />
      <section v-else class="page-content">
        <div class="page-heading">
          <div>
            <span class="eyebrow">YOUR ACCESS</span>
            <h1>你的账号与访问权限</h1>
            <p class="muted">
              当前登录为 {{ user.username }}，角色为{{
                roles[user.role] || user.role
              }}。
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
            <ArrowUpRight :size="15" />
          </article>
        </div>
        <p class="page-footnote">
          这些权限来自账号角色配置。如需调整，请联系管理员。
        </p>
      </section>
    </main>
  </div>
</template>
