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
        <h1>人工待办</h1>
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
    <div v-else class="table-card table-scroll">
      <table class="handoff-table">
        <thead>
          <tr>
            <th>编号 / 时间</th>
            <th>问题与转交原因</th>
            <th>发起人</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.id">
            <td>
              <span class="tag amber">待处理 · #{{ item.id }}</span
              ><small class="cell-detail">{{
                displayTime(item.created_at)
              }}</small>
            </td>
            <td class="handoff-question">
              <strong>{{ item.question }}</strong>
              <p>{{ item.reason }}</p>
            </td>
            <td>{{ item.created_by }}</td>
            <td>
              <button
                class="secondary"
                :disabled="busyId === item.id"
                @click="resolve(item.id)"
              >
                <Check :size="15" />{{
                  busyId === item.id ? "正在处理…" : "标记已处理"
                }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
