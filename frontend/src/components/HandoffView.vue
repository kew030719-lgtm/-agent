<script setup lang="ts">
import { onMounted, ref } from "vue";
import { Check, CircleCheck, RefreshCw } from "@lucide/vue";
import { api, displayTime, errorText } from "../api";
import type { Handoff } from "../types";

const items = ref<Handoff[]>([]),
  error = ref(""),
  loading = ref(true),
  busyId = ref(0);
async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    items.value = await api<Handoff[]>("/handoffs");
  } catch (err) {
    error.value = errorText(err);
  } finally {
    loading.value = false;
  }
}
async function resolve(id: number): Promise<void> {
  busyId.value = id;
  try {
    await api<void>(`/handoffs/${id}/resolve`, { method: "POST" });
    items.value = items.value.filter((item) => item.id !== id);
  } catch (err) {
    error.value = errorText(err);
  } finally {
    busyId.value = 0;
  }
}
onMounted(load);
</script>

<template>
  <section class="page-content">
    <div class="page-heading">
      <div>
        <span class="eyebrow">HUMAN SUPPORT</span>
        <h1>把需要帮助的人，接住。</h1>
        <p class="muted">查看助手转交的事项，线下处理完成后标记状态。</p>
      </div>
      <button class="secondary" :disabled="loading" @click="load">
        <RefreshCw :size="16" />刷新待办
      </button>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <div class="section-title">
      <h3>待处理事项</h3>
      <span class="tag">{{ items.length }} 项</span>
    </div>
    <div v-if="loading" class="empty-state">正在读取待办…</div>
    <div v-else-if="!items.length" class="empty-state table-card">
      <CircleCheck :size="36" />
      <h3>当前没有待处理事项</h3>
      <p>需要人工协助的问题会出现在这里。</p>
    </div>
    <div v-else class="handoff-list">
      <article v-for="item in items" :key="item.id" class="handoff-card">
        <div class="handoff-head">
          <span class="tag amber">待处理 · #{{ item.id }}</span
          ><time>{{ displayTime(item.created_at) }}</time>
        </div>
        <h3>{{ item.question }}</h3>
        <p>{{ item.reason }}</p>
        <div class="handoff-bottom">
          <span
            ><span class="mini-avatar">{{
              item.created_by.slice(0, 1).toUpperCase()
            }}</span
            >{{ item.created_by }}</span
          ><button
            class="secondary"
            :disabled="busyId === item.id"
            @click="resolve(item.id)"
          >
            <Check :size="15" />{{
              busyId === item.id ? "正在处理…" : "标记已处理"
            }}
          </button>
        </div>
      </article>
    </div>
  </section>
</template>
