<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import {
  ArrowRight,
  BookOpen,
  CalendarDays,
  Check,
  Headphones,
  RefreshCw,
  ThumbsDown,
  ThumbsUp,
  Wrench,
} from "@lucide/vue";
import { api, errorText } from "../api";
import {
  busy,
  chatError,
  loading,
  selectedId,
  selectConversation,
  sendQuestion,
  turns,
} from "../chat";
import type { ChatTurn, User } from "../types";

defineProps<{ user: User }>();
const draft = ref(""),
  feed = ref<HTMLElement>(),
  sendingQuestion = ref(""),
  feedbackBusy = ref(0);
const labels: Record<string, string> = {
  knowledge: "知识问答",
  identity: "员工助手",
  permissions: "账号权限",
  todos: "我的待办",
  notifications: "站内通知",
  it: "IT 服务",
  hr: "人事服务",
  finance: "财务服务",
  meeting: "会议室",
  human: "人工协助",
  clarify: "需要补充",
};
const pending = computed(() => turns.value.at(-1)?.status === "processing");
const needsHandoff = computed(() =>
  turns.value.at(-1)?.answer.includes("回复“转人工”"),
);

const confirmation = computed(() => turns.value.at(-1)?.confirmation || "");
async function send(text = draft.value): Promise<void> {
  const question = text.trim();
  if (!question || busy.value || loading.value) return;
  sendingQuestion.value = question;
  draft.value = "";
  await nextTick();
  feed.value?.scrollTo({ top: feed.value.scrollHeight, behavior: "smooth" });
  const success = await sendQuestion(question);
  if (!success) draft.value = question;
  sendingQuestion.value = "";
  await nextTick();
  feed.value?.scrollTo({ top: feed.value.scrollHeight, behavior: "smooth" });
}
async function rate(turn: ChatTurn, rating: string): Promise<void> {
  feedbackBusy.value = turn.id;
  try {
    const updated = await api<ChatTurn>(`/messages/${turn.id}/feedback`, {
      method: "POST",
      body: JSON.stringify({ rating }),
    });
    Object.assign(turn, updated);
  } catch (error) {
    chatError.value = errorText(error);
  } finally {
    feedbackBusy.value = 0;
  }
}
function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    void send();
  }
}
watch(selectedId, () => {
  draft.value = "";
});
</script>

<template>
  <section class="chat-workspace">
    <div ref="feed" class="chat-feed">
      <div v-if="!turns.length && !busy && !loading" class="welcome">
        <div class="welcome-heading">
          <h1>新建对话</h1>
          <p>查询公司资料，或办理日常事务。</p>
        </div>
        <h2 class="quick-title">常用操作</h2>
        <div class="prompt-grid">
          <button @click="send('公司的年假申请流程是什么？')">
            <BookOpen :size="19" /><span
              ><strong>公司制度</strong><small>查询制度与申请流程</small></span
            ><ArrowRight :size="16" />
          </button>
          <button @click="send('我的请假余额')">
            <CalendarDays :size="19" /><span
              ><strong>请假余额</strong><small>查看本人可用假期</small></span
            ><ArrowRight :size="16" />
          </button>
          <button @click="send('电脑无法联网')">
            <Wrench :size="19" /><span
              ><strong>IT 报修</strong><small>提交设备或网络问题</small></span
            ><ArrowRight :size="16" />
          </button>
          <button @click="send('查会议室')">
            <Headphones :size="19" /><span
              ><strong>会议室</strong><small>查询空闲时段与预订</small></span
            ><ArrowRight :size="16" />
          </button>
        </div>
        <p class="welcome-foot">
          也可以直接在下方输入问题。回答会附上可查阅的资料来源。
        </p>
      </div>
      <div v-if="loading" class="empty-state">正在读取对话…</div>
      <div v-for="turn in turns" :key="turn.id" class="turn">
        <div class="user-message">
          <span class="message-text">{{ turn.question }}</span
          ><span class="avatar">{{
            user.username.slice(0, 1).toUpperCase()
          }}</span>
        </div>
        <div class="assistant-message">
          <span class="assistant-avatar" aria-hidden="true">助</span>
          <div class="answer-body">
            <div class="answer-meta">
              <strong>工作助手</strong
              ><span class="tag">{{ labels[turn.domain] || "员工助手" }}</span>
            </div>
            <p v-if="turn.status === 'processing'" class="muted">
              请求尚未完成。可刷新查看结果；若服务已重启，请先核对业务记录再发起新对话。
            </p>
            <p
              v-else
              class="message-text"
              :class="{ 'failed-answer': turn.status === 'failed' }"
            >
              {{ turn.answer }}
            </p>
            <div v-if="turn.feedback_allowed" class="answer-actions">
              <span v-if="turn.rating" class="muted"
                ><Check :size="13" />已反馈：{{ turn.rating }}</span
              >
              <template v-else
                ><button
                  :disabled="feedbackBusy === turn.id"
                  title="这条回答有用"
                  aria-label="这条回答有用"
                  @click="rate(turn, '有用')"
                >
                  <ThumbsUp :size="15" /></button
                ><button
                  :disabled="feedbackBusy === turn.id"
                  title="这条回答无用"
                  aria-label="这条回答无用"
                  @click="rate(turn, '无用')"
                >
                  <ThumbsDown :size="15" /></button
                ><span>回答对你有帮助吗？</span></template
              >
            </div>
          </div>
        </div>
      </div>
      <div v-if="busy" class="turn">
        <div class="user-message">
          <span class="message-text">{{ sendingQuestion }}</span
          ><span class="avatar">{{
            user.username.slice(0, 1).toUpperCase()
          }}</span>
        </div>
        <div class="thinking"><span class="pulse"></span>正在处理…</div>
      </div>
    </div>
    <div class="composer-wrap">
      <div v-if="chatError" class="notice error" role="alert">
        {{ chatError }}
      </div>
      <div v-if="confirmation && !busy && !pending && !loading" class="confirm-actions">
        <span>核对上方内容后提交，可直接输入修改要求。</span
        ><button class="small-primary" @click="send(confirmation)">
          {{ confirmation }}</button
        ><button class="secondary" @click="send('取消')">取消</button>
      </div>
      <div v-if="needsHandoff && !busy && !pending" class="confirm-actions">
        <span>需要人工协助吗？</span>
        <button class="small-primary" @click="send('转人工')">转人工</button>
        <button class="secondary" @click="send('取消转人工')">暂不需要</button>
      </div>
      <div v-if="pending" class="notice">
        当前对话有请求处理中。<button
          class="text-button"
          @click="selectConversation(selectedId)"
        >
          <RefreshCw :size="14" />刷新结果
        </button>
      </div>
      <form class="composer" @submit.prevent="send()">
        <textarea
          v-model="draft"
          aria-label="输入员工问题"
          placeholder="输入问题，例如：年假申请需要哪些材料？"
          rows="2"
          maxlength="2000"
          :disabled="busy || loading || pending"
          @keydown="onKeydown"
        />
        <div class="composer-bottom">
          <span
            >Enter 发送 · Shift + Enter 换行 <i>·</i>
            {{ draft.length }}/2000</span
          ><button
            class="send-button"
            title="发送问题"
            aria-label="发送问题"
            :disabled="!draft.trim() || busy || loading || pending"
          >
            发送<ArrowRight :size="16" />
          </button>
        </div>
      </form>
    </div>
  </section>
</template>
