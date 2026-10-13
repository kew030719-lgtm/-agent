<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api, displayTime, errorText } from "../api";
import type { Feedback, Usage } from "../types";

const usage = ref<Usage[]>([]),
  feedback = ref<Feedback[]>([]),
  error = ref(""),
  loading = ref(true);
const answered = computed(() =>
  usage.value.find((item) => item.outcome === "answered"),
);
const handoffs = computed(() =>
  usage.value.find((item) => item.outcome === "handoff"),
);
onMounted(async (): Promise<void> => {
  try {
    [usage.value, feedback.value] = await Promise.all([
      api<Usage[]>("/knowledge-stats"),
      api<Feedback[]>("/knowledge-feedback"),
    ]);
  } catch (err) {
    error.value = errorText(err);
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <section class="page-content">
    <div class="page-heading">
      <div>
        <h1>反馈与统计</h1>
        <p class="muted">了解知识问答的使用情况，查看员工主动提交的评价。</p>
      </div>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <div v-if="loading" class="empty-state">正在加载统计…</div>
    <template v-else
      ><div class="metrics">
        <article>
          <span>知识回答成功</span
          ><strong>{{ answered?.count || 0 }}<small>次</small></strong>
          <p>通过资料与引用校验的回答</p>
        </article>
        <article>
          <span>知识问题转人工</span
          ><strong>{{ handoffs?.count || 0 }}<small>次</small></strong>
          <p>因知识依据不足而转交</p>
        </article>
        <article>
          <span>成功回答平均耗时</span
          ><strong
            >{{ ((answered?.average_ms || 0) / 1000).toFixed(1)
            }}<small>秒</small></strong
          >
          <p>不包含服务异常请求</p>
        </article>
      </div>
      <div class="table-card">
        <div class="table-toolbar">
          <strong>最近的回答评价</strong><span class="muted">最近 20 条</span>
        </div>
        <div v-if="!feedback.length" class="empty-state">
          <h3>还没有收到评价</h3>
          <p>员工可以在知识回答下方选择“有用”或“无用”。</p>
        </div>
        <article v-for="item in feedback" :key="item.id" class="feedback-item">
          <div class="handoff-head">
            <span
              class="tag"
              :class="item.rating === '有用' ? 'green' : 'amber'"
              >{{ item.rating }}</span
            ><time
              >{{ item.username }} · {{ displayTime(item.created_at) }}</time
            >
          </div>
          <h3>{{ item.question }}</h3>
          <p class="message-text">{{ item.answer }}</p>
        </article>
      </div></template
    >
  </section>
</template>
