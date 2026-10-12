<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  FileText,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  X,
} from "@lucide/vue";
import { api, displayTime, errorText } from "../api";
import type { KnowledgeFile, User } from "../types";

const props = defineProps<{ user: User }>();
const files = ref<KnowledgeFile[]>([]),
  search = ref(""),
  error = ref(""),
  notice = ref("");
const loading = ref(true),
  busy = ref(false),
  showUpload = ref(false),
  scope = ref("general");
const chosen = ref<File | null>(null),
  deleteTarget = ref<KnowledgeFile | null>(null);
const canWrite = computed(() => props.user.permissions.includes("kb:write"));
const scopes: Record<string, string> = {
  general: "全员公开",
  hr: "人事专属",
  it: "IT 专属",
  finance: "财务专属",
};
const filtered = computed(() =>
  files.value.filter((file) =>
    file.title.toLowerCase().includes(search.value.toLowerCase()),
  ),
);
async function load(): Promise<void> {
  loading.value = true;
  try {
    files.value = await api<KnowledgeFile[]>("/knowledge");
  } catch (err) {
    error.value = errorText(err);
  } finally {
    loading.value = false;
  }
}
function selectFile(event: Event): void {
  chosen.value = (event.target as HTMLInputElement).files?.[0] || null;
}
async function upload(): Promise<void> {
  if (!chosen.value) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  const form = new FormData();
  form.append("file", chosen.value);
  form.append("scope", scope.value);
  try {
    const result = await api<{ id: number; chunks: number }>("/knowledge", {
      method: "POST",
      body: form,
    });
    notice.value = `文件已导入，建立了 ${result.chunks} 个知识切片。`;
    showUpload.value = false;
    chosen.value = null;
  } catch (err) {
    error.value = errorText(err);
  } finally {
    busy.value = false;
    await load();
  }
}
async function reindex(file: KnowledgeFile): Promise<void> {
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    const result = await api<{ chunks: number }>(
      `/knowledge/${file.id}/reindex`,
      { method: "POST" },
    );
    notice.value = `索引检查完成，补建 ${result.chunks} 个切片。`;
  } catch (err) {
    error.value = errorText(err);
  } finally {
    busy.value = false;
  }
}
async function remove(): Promise<void> {
  if (!deleteTarget.value) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await api<void>(`/knowledge/${deleteTarget.value.id}`, {
      method: "DELETE",
    });
    deleteTarget.value = null;
    notice.value = "文件与检索索引已删除。";
    await load();
  } catch (err) {
    error.value = errorText(err);
  } finally {
    busy.value = false;
  }
}
onMounted(load);
</script>

<template>
  <section class="page-content">
    <div class="page-heading">
      <div>
        <span class="eyebrow">KNOWLEDGE LIBRARY</span>
        <h1>团队的知识，随时可查。</h1>
        <p class="muted">这里展示当前账号有权访问的公司资料。</p>
      </div>
      <button
        v-if="canWrite"
        class="primary"
        :disabled="busy"
        @click="showUpload = !showUpload"
      >
        <Plus :size="17" />导入知识
      </button>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice success" role="status">{{ notice }}</p>
    <form v-if="showUpload" class="upload-panel" @submit.prevent="upload">
      <div class="section-title">
        <h3>导入一份新资料</h3>
        <button
          class="icon-button"
          type="button"
          aria-label="关闭上传"
          :disabled="busy"
          @click="showUpload = false"
        >
          <X :size="18" />
        </button>
      </div>
      <label class="file-picker"
        ><Upload :size="23" /><span>{{
          chosen?.name || "选择需要导入的文件"
        }}</span
        ><small>PDF、Markdown、DOCX、Excel</small
        ><input
          type="file"
          aria-label="选择知识文件"
          accept=".pdf,.md,.docx,.xlsx,.xlsm,.xls"
          :disabled="busy"
          required
          @change="selectFile"
      /></label>
      <div class="upload-bottom">
        <label
          >可见范围<select v-model="scope" :disabled="busy">
            <option v-for="(label, key) in scopes" :key="key" :value="key">
              {{ label }}
            </option>
          </select></label
        ><button class="primary" :disabled="busy || !chosen">
          {{ busy ? "正在解析与建立索引…" : "开始导入" }}
        </button>
      </div>
    </form>
    <div class="table-card">
      <div class="table-toolbar">
        <strong
          >知识文件 <span class="count">{{ files.length }}</span></strong
        >
        <div class="search-field">
          <Search :size="16" /><input
            v-model="search"
            aria-label="搜索知识文件"
            placeholder="搜索文件名称…"
          />
        </div>
      </div>
      <div v-if="loading" class="empty-state">正在读取资料…</div>
      <div v-else-if="!filtered.length" class="empty-state">
        <FileText :size="32" />
        <h3>{{ search ? "没有匹配的文件" : "知识库还没有可见资料" }}</h3>
        <p>
          {{
            canWrite
              ? "导入一份制度或流程文件，开始有据可查的问答。"
              : "管理员导入你有权限查看的资料后，会显示在这里。"
          }}
        </p>
      </div>
      <div v-else class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>文件名称</th>
              <th>可见范围</th>
              <th>切片</th>
              <th>导入时间</th>
              <th v-if="canWrite">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="file in filtered" :key="file.id">
              <td>
                <div class="file-cell">
                  <span class="file-icon"><FileText :size="19" /></span>
                  <div>
                    <strong>{{ file.title }}</strong
                    ><small
                      >{{ file.source_format?.toUpperCase() || "TEXT" }} · #{{
                        file.id
                      }}</small
                    >
                  </div>
                </div>
              </td>
              <td>
                <span class="tag">{{ scopes[file.scope] }}</span>
              </td>
              <td>{{ file.chunks }}</td>
              <td class="muted">{{ displayTime(file.created_at) }}</td>
              <td v-if="canWrite">
                <div class="row-actions">
                  <button
                    class="icon-button"
                    title="补建缺失索引"
                    aria-label="补建缺失索引"
                    :disabled="busy"
                    @click="reindex(file)"
                  >
                    <RefreshCw :size="16" /></button
                  ><button
                    class="icon-button danger"
                    title="删除知识文件"
                    aria-label="删除知识文件"
                    :disabled="busy"
                    @click="deleteTarget = file"
                  >
                    <Trash2 :size="16" />
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <p class="page-footnote">
      文件可见范围由后端权限控制；导入后的资料会用于知识问答与来源引用。
    </p>
    <div v-if="deleteTarget" class="modal-backdrop">
      <div
        class="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-title"
      >
        <h2 id="delete-title">删除这份知识文件？</h2>
        <p>“{{ deleteTarget.title }}”的原文件、切片与检索索引都会被删除。</p>
        <div class="modal-actions">
          <button
            class="secondary"
            :disabled="busy"
            @click="deleteTarget = null"
          >
            取消</button
          ><button class="danger-button" :disabled="busy" @click="remove">
            {{ busy ? "正在删除…" : "确认删除" }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>
