<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
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
import KnowledgeAccessEditor from "./KnowledgeAccessEditor.vue";
import type { AccessPolicy, AccessOptions, KnowledgeFile, User } from "../types";

const props = defineProps<{ user: User }>();
const managed = ref(false);
const productTarget = ref<KnowledgeFile | null>(null);
const product = ref({ company: "", code: "", name: "" });
const isProduct = ref(false);
async function editProduct(file: KnowledgeFile): Promise<void> {
  error.value = "";
  try {
    product.value = await api<typeof product.value | null>(`/knowledge/${file.id}/product`) || { company: "", code: "", name: file.title };
    showUpload.value = false;
    productTarget.value = file;
  } catch (err) { error.value = errorText(err); }
}
async function saveProduct(): Promise<void> {
  if (!productTarget.value || busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await api(`/knowledge/${productTarget.value.id}/product`, { method: "POST", body: JSON.stringify(product.value) });
    productTarget.value = null;
    notice.value = "产品登记已保存。同一公司和产品编号的多份资料会合并统计。";
  } catch (err) { error.value = errorText(err); }
  finally { busy.value = false; }
}
const access = ref<AccessPolicy>({ mode: "restricted", groups: [] });
const accessOptions = ref<AccessOptions>({ departments: [], job_levels: [], users: [] });
const accessTarget = ref<KnowledgeFile | null>(null);
const previewData = ref<{ id: number; title: string; chunks: { id: number; text: string }[]; assets: number[] } | null>(null);
const validAccess = computed(() => access.value.groups.every((group) =>
  group.department_ids.length || group.min_job_level || group.usernames.length));
async function openUpload(): Promise<void> {
  error.value = "";
  try {
    accessOptions.value = await api<AccessOptions>("/knowledge/access-options");
    access.value = { mode: "restricted", groups: [] };
    isProduct.value = false;
    product.value = { company: "", code: "", name: "" };
    showUpload.value = true;
  } catch (err) { error.value = errorText(err); }
}
async function editAccess(file: KnowledgeFile): Promise<void> {
  error.value = "";
  try {
    [accessOptions.value, access.value] = await Promise.all([
      api<AccessOptions>("/knowledge/access-options"), api<AccessPolicy>(`/knowledge/${file.id}/access`),
    ]);
    showUpload.value = false;
    accessTarget.value = file;
  } catch (err) { error.value = errorText(err); }
}
async function saveAccess(): Promise<void> {
  if (!accessTarget.value || busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await api(`/knowledge/${accessTarget.value.id}/access`, { method: "POST", body: JSON.stringify(access.value) });
    accessTarget.value = null;
    notice.value = "访问规则已保存，即时生效。无需重新计算向量。";
    await load();
  } catch (err) { error.value = errorText(err); }
  finally { busy.value = false; }
}
async function preview(file: KnowledgeFile): Promise<void> {
  error.value = "";
  try { previewData.value = await api(`/knowledge/${file.id}/preview`); }
  catch (err) { error.value = errorText(err); }
}
watch(managed, load);
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
    files.value = await api<KnowledgeFile[]>(`/knowledge?managed=${managed.value}`);
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
  form.append("access_policy", JSON.stringify(access.value));
  if (isProduct.value) form.append("product_metadata", JSON.stringify(product.value));
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
        <h1>知识库</h1>
        <p class="muted">这里展示当前账号有权访问的公司资料。</p>
      </div>
      <button
        v-if="canWrite"
        class="primary"
        :disabled="busy"
        @click="openUpload"
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
      <label><input v-model="isProduct" type="checkbox" :disabled="busy" />这是一份产品资料，需要登记到产品目录</label>
      <div v-if="isProduct" class="product-fields">
        <label>公司名称<input v-model="product.company" required maxlength="100" :disabled="busy" /></label>
        <label>产品编号<input v-model="product.code" required maxlength="100" pattern="[A-Za-z0-9][A-Za-z0-9._\-]*" :disabled="busy" /></label>
        <label>产品名称<input v-model="product.name" required maxlength="200" :disabled="busy" /></label>
      </div>
      <KnowledgeAccessEditor v-model="access" :options="accessOptions" :disabled="busy" />
      <div class="upload-bottom">
        <label v-if="access.mode === 'legacy'"
          >原权限角色范围<select v-model="scope" :disabled="busy">
            <option v-for="(label, key) in scopes" :key="key" :value="key">
              {{ label }}
            </option>
          </select></label
        ><button class="primary" :disabled="busy || !chosen || !validAccess">
          {{ busy ? "正在解析与建立索引…" : "开始导入" }}
        </button>
      </div>
    </form>
    <div v-if="canWrite" class="row-actions knowledge-tabs">
      <button :class="!managed ? 'primary' : 'secondary'" :disabled="busy" @click="managed = false">我可阅读的资料</button>
      <button :class="managed ? 'primary' : 'secondary'" :disabled="busy" @click="managed = true">我负责的资料</button>
    </div>
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
              <th>操作</th>
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
                <span class="tag">{{ file.access_mode === "legacy" ? scopes[file.scope] + "（原权限）" : file.access_mode === "all" ? "全体员工" : "自定义授权" }}</span>
                <small v-if="!file.can_read">正文未授权</small>
              </td>
              <td>{{ file.chunks }}</td>
              <td class="muted">{{ displayTime(file.created_at) }}</td>
              <td>
                <div class="row-actions">
                  <button v-if="file.can_read" class="secondary" :disabled="busy" @click="preview(file)">查看</button>
                  <a v-if="file.can_read && file.source_format" :href="`/api/knowledge/${file.id}/download`" class="secondary">下载</a>
                  <button v-if="file.can_manage" class="secondary" :disabled="busy" @click="editAccess(file)">权限</button>
                  <button v-if="file.can_manage" class="secondary" :disabled="busy" @click="editProduct(file)">产品登记</button>
                  <button v-if="file.can_manage"
                    class="icon-button"
                    title="补建缺失索引"
                    aria-label="补建缺失索引"
                    :disabled="busy"
                    @click="reindex(file)"
                  >
                    <RefreshCw :size="16" /></button
                  ><button v-if="file.can_manage"
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
    <div v-if="productTarget" class="modal-backdrop">
      <form class="modal knowledge-modal" role="dialog" aria-modal="true" aria-label="产品目录登记" @submit.prevent="saveProduct">
        <h2>{{ productTarget.title }} · 产品登记</h2>
        <p class="muted">只有已登记且当前账号有权阅读的产品资料参与统计。相同公司与编号计为一个产品。</p>
        <div class="product-fields">
          <label>公司名称<input v-model="product.company" required maxlength="100" :disabled="busy" /></label>
          <label>产品编号<input v-model="product.code" required maxlength="100" pattern="[A-Za-z0-9][A-Za-z0-9._\-]*" :disabled="busy" /></label>
          <label>产品名称<input v-model="product.name" required maxlength="200" :disabled="busy" /></label>
        </div>
        <p v-if="error" class="notice error">{{ error }}</p>
        <div class="modal-actions"><button class="secondary" type="button" :disabled="busy" @click="productTarget = null">取消</button><button class="primary" :disabled="busy">保存登记</button></div>
      </form>
    </div>
    <div v-if="accessTarget" class="modal-backdrop">
      <form class="modal knowledge-modal" role="dialog" aria-modal="true" aria-label="编辑文档权限" @submit.prevent="saveAccess">
        <h2>{{ accessTarget.title }} · 访问规则</h2>
        <KnowledgeAccessEditor v-model="access" :options="accessOptions" :disabled="busy" />
        <p v-if="error" class="notice error">{{ error }}</p>
        <div class="modal-actions">
          <button class="secondary" type="button" :disabled="busy" @click="accessTarget = null">取消</button>
          <button class="primary" :disabled="busy || !validAccess">{{ busy ? "正在保存…" : "保存权限" }}</button>
        </div>
      </form>
    </div>
    <div v-if="previewData" class="modal-backdrop">
      <div class="modal knowledge-modal" role="dialog" aria-modal="true" aria-label="文档预览">
        <div class="section-title"><h2>{{ previewData.title }}</h2><button class="secondary" @click="previewData = null">关闭</button></div>
        <p v-for="chunk in previewData.chunks" :key="chunk.id" class="preview-text">{{ chunk.text }}</p>
        <img v-for="id in previewData.assets" :key="id" :src="`/api/knowledge/assets/${id}`" alt="文档图片" class="preview-image" />
      </div>
    </div>
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

<style scoped>
.product-fields { display: grid; gap: 12px; margin: 16px 0; }
.product-fields label { display: grid; gap: 6px; }
.knowledge-tabs { margin: 18px 0; }
.knowledge-modal { max-height: calc(100dvh - 48px); overflow-y: auto; width: min(640px, calc(100vw - 32px)); }
.preview-text { white-space: pre-wrap; line-height: 1.8; overflow-wrap: anywhere; }
.preview-image { max-width: 100%; }
td small { display: block; color: var(--muted); margin-top: 5px; }
</style>
