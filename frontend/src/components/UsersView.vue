<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Plus, RefreshCw, Search, ShieldCheck, X } from "@lucide/vue";
import { api, errorText } from "../api";
import type { ManagedUser, RoleOption } from "../types";

const users = ref<ManagedUser[]>([]);
const roles = ref<RoleOption[]>([]);
const loading = ref(true),
  saving = ref(false),
  showCreate = ref(false);
const error = ref(""),
  formError = ref(""),
  notice = ref(""),
  search = ref("");
const username = ref(""),
  password = ref(""),
  confirmation = ref(""),
  role = ref("employee");
const roleNames: Record<string, string> = {
  employee: "普通员工",
  hr: "人事",
  it: "IT",
  finance: "财务",
  admin: "管理员",
};
const roleDescriptions: Record<string, string> = {
  employee: "查询公共知识和本人余额，提交工单与报销，查询和预订会议室。",
  hr: "在员工权限基础上，访问人事知识并维护请假余额。",
  it: "在员工权限基础上，访问 IT 知识并获得工单读取权限。",
  finance: "在员工权限基础上，访问财务知识并获得报销读取权限。",
  admin: "管理用户、知识库、人工待办及其他管理员功能。",
};
const filtered = computed(() =>
  users.value.filter((user) =>
    user.username.toLowerCase().includes(search.value.toLowerCase()),
  ),
);
const selectedRole = computed(() =>
  roles.value.find((item) => item.role === role.value),
);

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    [users.value, roles.value] = await Promise.all([
      api<ManagedUser[]>("/users"),
      api<RoleOption[]>("/users/roles"),
    ]);
  } catch (err) {
    error.value = errorText(err);
  } finally {
    loading.value = false;
  }
}

function openCreate(): void {
  username.value = "";
  password.value = "";
  confirmation.value = "";
  role.value = "employee";
  formError.value = "";
  notice.value = "";
  showCreate.value = true;
}

function closeCreate(): void {
  showCreate.value = false;
  password.value = "";
  confirmation.value = "";
}

async function createUser(): Promise<void> {
  if (saving.value) return;
  formError.value = "";
  if (!username.value.trim()) {
    formError.value = "请输入账号名。";
    return;
  }
  if (password.value !== confirmation.value) {
    formError.value = "两次输入的密码不一致。";
    return;
  }
  saving.value = true;
  try {
    const created = await api<ManagedUser>("/users", {
      method: "POST",
      body: JSON.stringify({
        username: username.value.trim(),
        password: password.value,
        role: role.value,
      }),
    });
    users.value = [...users.value, created].sort((a, b) =>
      a.username.localeCompare(b.username),
    );
    notice.value = `账号「${created.username}」已创建，角色为${roleNames[created.role]}，现在可以登录。`;
    closeCreate();
  } catch (err) {
    formError.value = errorText(err);
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <section class="page-content">
    <div class="page-heading">
      <div>
        <span class="eyebrow">TEAM & ACCESS</span>
        <h1>让每位同事，进入工作空间。</h1>
        <p class="muted">创建员工账号，按工作职责分配角色与访问权限。</p>
      </div>
      <button
        class="primary"
        :disabled="loading || !roles.length"
        @click="openCreate"
      >
        <Plus :size="17" />创建账号
      </button>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice success" role="status">{{ notice }}</p>
    <div class="table-card">
      <div class="table-toolbar">
        <strong
          >用户列表 <span class="count">{{ users.length }}</span></strong
        >
        <div class="row-actions">
          <div class="search-field">
            <Search :size="16" /><input
              v-model="search"
              aria-label="搜索用户"
              placeholder="搜索账号…"
            />
          </div>
          <button
            class="icon-button"
            aria-label="刷新用户列表"
            :disabled="loading"
            @click="load"
          >
            <RefreshCw :size="17" />
          </button>
        </div>
      </div>
      <div v-if="loading" class="empty-state">正在读取账号…</div>
      <div v-else-if="!filtered.length" class="empty-state">没有匹配的账号</div>
      <div v-else class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>员工账号</th>
              <th>角色</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in filtered" :key="item.username">
              <td>
                <div class="file-cell">
                  <span class="avatar">{{
                    item.username.slice(0, 1).toUpperCase()
                  }}</span
                  ><strong>{{ item.username }}</strong>
                </div>
              </td>
              <td>{{ roleNames[item.role] || item.role }}</td>
              <td>
                <span class="tag" :class="item.enabled ? 'green' : 'amber'">{{
                  item.enabled ? "已启用" : "已停用"
                }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <p class="page-footnote">
      仅有用户管理权限的账号可以创建用户。新用户可直接使用初始密码登录，权限由后端按角色校验。
    </p>
    <div v-if="showCreate" class="modal-backdrop">
      <form
        class="modal user-create-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-user-title"
        @submit.prevent="createUser"
      >
        <div class="section-title">
          <h2 id="create-user-title">创建一个新账号</h2>
          <button
            class="icon-button"
            type="button"
            aria-label="关闭创建账号"
            :disabled="saving"
            @click="closeCreate"
          >
            <X :size="18" />
          </button>
        </div>
        <label
          >账号名<input
            v-model="username"
            autocomplete="off"
            placeholder="例如：zhangsan"
            required
            maxlength="100"
            :disabled="saving"
        /></label>
        <label
          >角色<select v-model="role" :disabled="saving">
            <option
              v-for="option in roles"
              :key="option.role"
              :value="option.role"
            >
              {{ roleNames[option.role] || option.role }}
            </option>
          </select></label
        >
        <div class="role-description">
          <ShieldCheck :size="17" /><span
            >{{ roleDescriptions[role]
            }}<small
              >该角色拥有
              {{ selectedRole?.permissions.length || 0 }} 项权限。</small
            ></span
          >
        </div>
        <label
          >初始密码<input
            v-model="password"
            type="password"
            autocomplete="new-password"
            minlength="8"
            maxlength="256"
            placeholder="至少 8 位"
            required
            :disabled="saving"
        /></label>
        <label
          >确认密码<input
            v-model="confirmation"
            type="password"
            autocomplete="new-password"
            minlength="8"
            maxlength="256"
            placeholder="再次输入初始密码"
            required
            :disabled="saving"
        /></label>
        <p v-if="formError" class="notice error" role="alert">
          {{ formError }}
        </p>
        <div class="modal-actions">
          <button
            class="secondary"
            type="button"
            :disabled="saving"
            @click="closeCreate"
          >
            取消</button
          ><button class="primary" :disabled="saving || !roles.length">
            {{ saving ? "正在创建…" : "创建账号" }}
          </button>
        </div>
      </form>
    </div>
  </section>
</template>

<style scoped>
.user-create-modal {
  max-height: calc(100dvh - 40px);
  overflow-y: auto;
}
.user-create-modal h2 {
  margin-bottom: 0;
}
.user-create-modal > label {
  margin: 15px 0;
}
.role-description {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  color: #758764;
  background: #f3f6ec;
  padding: 12px;
  border-radius: 8px;
  font-size: 11px;
  line-height: 1.8;
}
.role-description svg {
  flex-shrink: 0;
  margin-top: 2px;
}
.role-description small {
  display: block;
  color: #91a07f;
  margin-top: 4px;
}
.user-create-modal .notice {
  margin-top: 16px;
}
</style>
