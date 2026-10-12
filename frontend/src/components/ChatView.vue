<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import {
  ArrowUp,
  BookOpen,
  CalendarDays,
  Check,
  Headphones,
  Layers3,
  RefreshCw,
  ShieldCheck,
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
  it: "IT 服务",
  hr: "人事服务",
  finance: "财务服务",
  meeting: "会议室",
  human: "人工协助",
};
const pending = computed(() => turns.value.at(-1)?.status === "processing");
const needsConfirmation = computed(() =>
  turns.value.at(-1)?.answer.includes("回复“确认报修”"),
);
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
        <span class="welcome-icon"
          ><Layers3 :size="30" :stroke-width="1.5"
        /></span>
        <span class="eyebrow">YOUR EVERYDAY WORK COMPANION</span>
        <h1>
          你好，{{ user.username }}<span>。</span><br />今天有什么可以帮你？
        </h1>
        <p>
          查制度、办事务，或理清一个工作问题。<br
            class="mobile-only"
          />从一句话开始。
        </p>
        <div class="prompt-grid">
          <button @click="send('公司的年假申请流程是什么？')">
            <BookOpen :size="21" /><strong>查一查公司制度</strong
            ><span>了解年假申请流程 <span>↗</span></span>
          </button>
          <button @click="send('我的请假余额')">
            <CalendarDays :size="21" /><strong>查看我的请假余额</strong
            ><span>查询当前可用天数 <span>↗</span></span>
          </button>
          <button @click="send('电脑无法联网')">
            <Wrench :size="21" /><strong>解决 IT 问题</strong
            ><span>描述故障，协助报修 <span>↗</span></span>
          </button>
          <button @click="send('查会议室')">
            <Headphones :size="21" /><strong>找一间会议室</strong
            ><span>查询位置与容纳人数 <span>↗</span></span>
          </button>
        </div>
        <div class="welcome-foot">
          <ShieldCheck
            :size="15"
          />回答依据你有权访问的资料，缺少依据时转人工协助。
        </div>
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
          <span class="assistant-avatar"><Layers3 :size="18" /></span>
          <div class="answer-body">
            <div class="answer-meta">
              <strong>栖知</strong
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
        <div class="thinking">
          <span class="pulse"></span>正在理解问题并处理，请稍候…
        </div>
      </div>
    </div>
    <div class="composer-wrap">
      <div v-if="chatError" class="notice error" role="alert">
        {{ chatError }}
      </div>
      <div v-if="needsConfirmation && !busy" class="confirm-actions">
        <span>是否提交这条报修？</span
        ><button class="small-primary" @click="send('确认报修')">
          确认报修</button
        ><button class="secondary" @click="send('取消')">取消</button>
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
          placeholder="问问公司制度，或告诉我你需要办理的事务…"
          rows="2"
          maxlength="2000"
          :disabled="busy || loading || pending"
          @keydown="onKeydown"
        />
        <div class="composer-bottom">
          <span
            ><span class="dot"></span> 企业员工助手 <i>·</i>
            {{ draft.length }}/2000</span
          ><button
            class="send-button"
            title="发送问题"
            aria-label="发送问题"
            :disabled="!draft.trim() || busy || loading || pending"
          >
            <ArrowUp :size="20" />
          </button>
        </div>
      </form>
      <p class="composer-note">
        Enter 发送，Shift + Enter 换行 · 涉及业务操作时，请核对处理结果。
      </p>
    </div>
  </section>
</template>
