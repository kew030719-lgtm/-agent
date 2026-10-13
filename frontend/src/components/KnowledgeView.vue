<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
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
import KnowledgeJobs from "./KnowledgeJobs.vue";
import type { AccessPolicy, AccessOptions, KnowledgeFile, KnowledgeJob, User } from "../types";

const props = defineProps<{ user: User }>();
const managed = ref(false);
const showJobs = ref(false);
const jobsRefresh = ref(0);
let generation = 0;
let loadRequest = 0;
const pendingKeys = new Map<string, string>();
const indexLabels: Record<KnowledgeFile["index_status"], string> = {
  pending: "等待建库", processing: "建库中", failed: "建库失败", ready: "可检索",
};
const productTarget = ref<KnowledgeFile | null>(null);
const product = ref({ company: "", code: "", name: "" });
const isProduct = ref(false);
async function editProduct(file: KnowledgeFile): Promise<void> {
  const current = generation;
  error.value = "";
  try {
    const result = await api<typeof product.value | null>(`/knowledge/${file.id}/product`);
    if (current !== generation) return;
    product.value = result || { company: "", code: "", name: file.title };
    showUpload.value = false;
    productTarget.value = file;
  } catch (err) { if (current === generation) error.value = errorText(err); }
}
async function saveProduct(): Promise<void> {
  if (!productTarget.value || busy.value) return;
  const current = generation;
  busy.value = true;
  error.value = "";
  try {
    await api(`/knowledge/${productTarget.value.id}/product`, { method: "POST", body: JSON.stringify(product.value) });
    if (current !== generation) return;
    productTarget.value = null;
    notice.value = "产品登记已保存。同一公司和产品编号的多份资料会合并统计。";
  } catch (err) { if (current === generation) error.value = errorText(err); }
  finally { if (current === generation) busy.value = false; }
}
const access = ref<AccessPolicy>({ mode: "restricted", groups: [] });
const accessOptions = ref<AccessOptions>({ departments: [], job_levels: [], users: [] });
const accessTarget = ref<KnowledgeFile | null>(null);
const previewData = ref<{ id: number; title: string; chunks: { id: number; text: string }[]; assets: number[] } | null>(null);
const validAccess = computed(() => access.value.groups.every((group) =>
  group.department_ids.length || group.min_job_level || group.usernames.length));
async function openUpload(): Promise<void> {
  const current = generation;
  error.value = "";
  try {
    const result = await api<AccessOptions>("/knowledge/access-options");
    if (current !== generation) return;
    accessOptions.value = result;
    access.value = { mode: "restricted", groups: [] };
    isProduct.value = false;
    product.value = { company: "", code: "", name: "" };
    showUpload.value = true;
  } catch (err) { if (current === generation) error.value = errorText(err); }
}
async function editAccess(file: KnowledgeFile): Promise<void> {
  const current = generation;
  error.value = "";
  try {
    const [options, policy] = await Promise.all([
      api<AccessOptions>("/knowledge/access-options"), api<AccessPolicy>(`/knowledge/${file.id}/access`),
    ]);
    if (current !== generation) return;
    accessOptions.value = options;
    access.value = policy;
    showUpload.value = false;
    accessTarget.value = file;
  } catch (err) { if (current === generation) error.value = errorText(err); }
}
async function saveAccess(): Promise<void> {
  if (!accessTarget.value || busy.value) return;
  const current = generation;
  busy.value = true;
  error.value = "";
  try {
    await api(`/knowledge/${accessTarget.value.id}/access`, { method: "POST", body: JSON.stringify(access.value) });
    if (current !== generation) return;
    accessTarget.value = null;
    notice.value = "访问规则已保存，即时生效。无需重新计算向量。";
    await load();
  } catch (err) { if (current === generation) error.value = errorText(err); }
  finally { if (current === generation) busy.value = false; }
}
async function preview(file: KnowledgeFile): Promise<void> {
  const current = generation;
  error.value = "";
  try {
    const result = await api<typeof previewData.value>(`/knowledge/${file.id}/preview`);
    if (current === generation) previewData.value = result;
  } catch (err) { if (current === generation) error.value = errorText(err); }
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
  const current = generation;
  const request = ++loadRequest;
  loading.value = true;
  try {
    const result = await api<KnowledgeFile[]>(`/knowledge?managed=${managed.value}`);
    if (current === generation && request === loadRequest) files.value = result;
  } catch (err) {
    if (current === generation && request === loadRequest) error.value = errorText(err);
  } finally {
    if (current === generation && request === loadRequest) loading.value = false;
  }
}
function requestKey(storageKey: string): string {
  let key = pendingKeys.get(storageKey);
  try { key = key || sessionStorage.getItem(storageKey) || undefined; } catch { /* Storage can be disabled by the browser. */ }
  if (!key) key = crypto.randomUUID();
  pendingKeys.set(storageKey, key);
  try { sessionStorage.setItem(storageKey, key); } catch { /* The in-memory key still covers a retry in this page. */ }
  return key;
}
function clearRequestKey(storageKey: string): void {
  pendingKeys.delete(storageKey);
  try { sessionStorage.removeItem(storageKey); } catch { /* Storage is optional. */ }
}
async function uploadKey(file: File, settings: string): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  const digest = Array.from(new Uint8Array(hash), (byte): string => byte.toString(16).padStart(2, "0")).join("");
  const settingsHash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify([file.name, digest, settings])));
  const fingerprint = Array.from(new Uint8Array(settingsHash), (byte): string => byte.toString(16).padStart(2, "0")).join("");
  return `knowledge-import:${props.user.username}:${fingerprint}`;
}
function jobCreated(job: KnowledgeJob): void {
  notice.value = job.status === "succeeded"
    ? "后台建库任务已完成，资料已可检索。"
    : "后台建库任务已创建，可在导入任务中查看进度。";
  showJobs.value = true;
  jobsRefresh.value += 1;
}
function selectFile(event: Event): void {
  chosen.value = (event.target as HTMLInputElement).files?.[0] || null;
}
async function upload(): Promise<void> {
  if (!chosen.value || busy.value) return;
  const current = generation;
  const file = chosen.value;
  busy.value = true;
  error.value = "";
  notice.value = "";
  const form = new FormData();
  form.append("file", file);
  form.append("scope", scope.value);
  form.append("access_policy", JSON.stringify(access.value));
  if (isProduct.value) form.append("product_metadata", JSON.stringify(product.value));
  try {
    const storageKey = await uploadKey(file, JSON.stringify([scope.value, access.value, isProduct.value ? product.value : null]));
    if (current !== generation) return;
    form.append("request_key", requestKey(storageKey));
    const result = await api<KnowledgeJob>("/knowledge", {
      method: "POST",
      body: form,
    });
    if (current !== generation) return;
    clearRequestKey(storageKey);
    jobCreated(result);
    showUpload.value = false;
    chosen.value = null;
  } catch (err) {
    if (current === generation) error.value = errorText(err);
  } finally {
    if (current === generation) {
      busy.value = false;
      await load();
    }
  }
}
async function reindex(file: KnowledgeFile): Promise<void> {
  if (busy.value) return;
  const current = generation;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    const storageKey = `knowledge-reindex:${props.user.username}:${file.id}`;
    const result = await api<KnowledgeJob>(
      `/knowledge/${file.id}/reindex`,
      { method: "POST", body: JSON.stringify({ request_key: requestKey(storageKey) }) },
    );
    if (current !== generation) return;
    clearRequestKey(storageKey);
    jobCreated(result);
    await load();
  } catch (err) {
    if (current === generation) error.value = errorText(err);
  } finally {
    if (current === generation) busy.value = false;
  }
}
async function remove(): Promise<void> {
  if (!deleteTarget.value || busy.value) return;
  const current = generation;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await api<void>(`/knowledge/${deleteTarget.value.id}`, {
      method: "DELETE",
    });
    if (current !== generation) return;
    deleteTarget.value = null;
    notice.value = "文件与检索索引已删除。";
    await load();
  } catch (err) {
    if (current === generation) error.value = errorText(err);
  } finally {
    if (current === generation) busy.value = false;
  }
}
function clearAccountState(): void {
  generation += 1;
  files.value = [];
  previewData.value = null;
  accessTarget.value = null;
  productTarget.value = null;
  deleteTarget.value = null;
  chosen.value = null;
  accessOptions.value = { departments: [], job_levels: [], users: [] };
  access.value = { mode: "restricted", groups: [] };
  product.value = { company: "", code: "", name: "" };
  showUpload.value = false;
  showJobs.value = false;
  busy.value = false;
  pendingKeys.clear();
  error.value = "";
  notice.value = "";
}
function refreshVisible(): void {
  if (document.visibilityState === "visible") void load();
}
watch(() => [props.user.username, props.user.permissions.join(",")], (): void => {
  clearAccountState();
  void load();
});
onMounted((): void => {
  window.addEventListener("session-expired", clearAccountState);
  window.addEventListener("focus", refreshVisible);
  void load();
});
onBeforeUnmount((): void => {
  clearAccountState();
  window.removeEventListener("session-expired", clearAccountState);
  window.removeEventListener("focus", refreshVisible);
});
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
          {{ busy ? "正在上传并创建任务…" : "提交后台建库" }}
        </button>
      </div>
      <p class="muted">上传完成后将在后台解析与建库。若网络中断，请保留当前配置重试；同一浏览器标签页刷新后，重新选择原文件和配置也会复用未确认的提交。</p>
    </form>
    <div v-if="canWrite" class="row-actions knowledge-tabs">
      <button :class="!managed && !showJobs ? 'primary' : 'secondary'" :disabled="busy" @click="managed = false; showJobs = false">我可阅读的资料</button>
      <button :class="managed && !showJobs ? 'primary' : 'secondary'" :disabled="busy" @click="managed = true; showJobs = false">我负责的资料</button>
      <button :class="showJobs ? 'primary' : 'secondary'" :disabled="busy" @click="showJobs = true; jobsRefresh += 1">导入任务</button>
    </div>
    <KnowledgeJobs v-if="canWrite" v-show="showJobs" :key="user.username" :username="user.username" :refresh-token="jobsRefresh" @settled="load" />
    <div v-show="!showJobs" class="table-card">
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
              <th>建库状态</th>
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
                <small v-if="file.index_status !== 'ready'">建库完成后按访问规则开放阅读</small>
                <small v-else-if="!file.can_read">正文未授权</small>
              </td>
              <td>{{ file.chunks }}</td>
              <td><span class="tag">{{ indexLabels[file.index_status] || '状态待确认' }}</span></td>
              <td class="muted">{{ displayTime(file.created_at) }}</td>
              <td>
                <div class="row-actions">
                  <button v-if="file.can_read && file.index_status === 'ready'" class="secondary" :disabled="busy" @click="preview(file)">查看</button>
                  <a v-if="file.can_read && file.source_format && file.index_status === 'ready'" :href="`/api/knowledge/${file.id}/download`" class="secondary">下载</a>
                  <button v-if="file.can_manage" class="secondary" :disabled="busy" @click="editAccess(file)">权限</button>
                  <button v-if="file.can_manage" class="secondary" :disabled="busy" @click="editProduct(file)">产品登记</button>
                  <button v-if="file.can_manage"
                    class="icon-button"
                    title="补建缺失索引"
                    aria-label="补建缺失索引"
                    :disabled="busy || file.index_status !== 'ready'"
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
      文件可见范围由后端权限控制；建库完成的资料才会用于知识问答与来源引用。
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
