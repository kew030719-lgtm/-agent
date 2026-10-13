<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Plus, RefreshCw, Search, ShieldCheck, X } from "@lucide/vue";
import { api, errorText } from "../api";
import type { ManagedUser, OrganizationOptions, RoleOption } from "../types";

const users = ref<ManagedUser[]>([]);
const roles = ref<RoleOption[]>([]);
const organization = ref<OrganizationOptions>({ departments: [], job_levels: [] });
const editing = ref(false);
const displayName = ref("");
const departmentId = ref<number | null>(null);
const jobLevel = ref("employee");
const managerUsername = ref<string | null>(null);
const departmentFilter = ref<number | null>(null);
const newDepartment = ref("");
const emit = defineEmits<{ saved: [] }>();
const levelNames: Record<string, string> = {
  employee: "普通员工", team_lead: "组长", department_manager: "部门经理",
  general_manager: "总经理", chairman: "董事长",
};
const managers = computed(() => {
  const ranks = organization.value.job_levels.map((item) => item.value);
  return users.value.filter((item) => item.enabled && item.username !== username.value &&
    ranks.indexOf(item.job_level) > ranks.indexOf(jobLevel.value) &&
    (["general_manager", "chairman"].includes(item.job_level) ||
      (departmentId.value !== null && item.department_id === departmentId.value)));
});
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
  employee: "基础权限",
  hr: "人事业务权限",
  it: "IT 业务权限",
  finance: "财务业务权限",
  admin: "系统管理员",
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
    (departmentFilter.value === null || user.department_id === departmentFilter.value) &&
    [user.username, user.display_name, user.department_name || "", levelNames[user.job_level]]
      .join(" ").toLowerCase().includes(search.value.toLowerCase()),
  ),
);
const selectedRole = computed(() =>
  roles.value.find((item) => item.role === role.value),
);

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    [users.value, roles.value, organization.value] = await Promise.all([
      api<ManagedUser[]>("/users"),
      api<RoleOption[]>("/users/roles"),
      api<OrganizationOptions>("/users/organization"),
    ]);
  } catch (err) {
    error.value = errorText(err);
  } finally {
    loading.value = false;
  }
}

function openCreate(): void {
  editing.value = false;
  displayName.value = "";
  departmentId.value = null;
  jobLevel.value = "employee";
  managerUsername.value = null;
  username.value = "";
  password.value = "";
  confirmation.value = "";
  role.value = "employee";
  formError.value = "";
  notice.value = "";
  showCreate.value = true;
}

function openEdit(item: ManagedUser): void {
  openCreate();
  editing.value = true;
  username.value = item.username;
  role.value = item.role;
  displayName.value = item.display_name;
  departmentId.value = item.department_id;
  jobLevel.value = item.job_level;
  managerUsername.value = item.manager_username;
}

async function addDepartment(): Promise<void> {
  if (saving.value || !newDepartment.value.trim()) return;
  saving.value = true;
  formError.value = "";
  try {
    const department = await api<{ id: number; name: string }>("/users/departments", {
      method: "POST", body: JSON.stringify({ name: newDepartment.value.trim() }),
    });
    organization.value.departments.push(department);
    departmentId.value = department.id;
    newDepartment.value = "";
  } catch (err) { formError.value = errorText(err); }
  finally { saving.value = false; }
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
  if (!editing.value && password.value !== confirmation.value) {
    formError.value = "两次输入的密码不一致。";
    return;
  }
  saving.value = true;
  try {
    const fields = {
      display_name: displayName.value.trim(), department_id: departmentId.value,
      job_level: jobLevel.value, manager_username: managerUsername.value, role: role.value,
    };
    const created = await api<ManagedUser>(editing.value ? `/users/${encodeURIComponent(username.value)}/profile` : "/users", {
      method: "POST",
      body: JSON.stringify(editing.value ? fields : {
        ...fields, username: username.value.trim(), password: password.value,
      }),
    });
    users.value = [...users.value.filter((item) => item.username !== created.username), created]
      .sort((a, b) => a.username.localeCompare(b.username));
    notice.value = `账号「${created.username}」的${editing.value ? "组织与权限信息已更新" : "档案已创建，可以登录"}。`;
    emit("saved");
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
        <h1>员工与组织</h1>
        <p class="muted">管理姓名、部门、职级和直属上级，单独分配系统访问权限。</p>
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
          <select v-model="departmentFilter" aria-label="按部门筛选">
            <option :value="null">全部部门</option>
            <option v-for="dep in organization.departments" :key="dep.id" :value="dep.id">{{ dep.name }}</option>
          </select>
          <div class="search-field">
            <Search :size="16" /><input
              v-model="search"
              aria-label="搜索用户"
              placeholder="搜索账号、姓名、职级…"
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
              <th>部门</th><th>职级</th><th>直属上级</th><th>权限角色</th>
              <th>状态</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in filtered" :key="item.username">
              <td>
                <div class="file-cell">
                  <span class="avatar">{{
                    item.username.slice(0, 1).toUpperCase()
                  }}</span
                  ><span><strong>{{ item.display_name || item.username }}</strong><small class="account-name">{{ item.username }}</small></span>
                </div>
              </td>
              <td>{{ item.department_name || "未分配" }}</td>
              <td>{{ levelNames[item.job_level] || item.job_level }}</td>
              <td>{{ item.manager_username || "未分配" }}</td>
              <td>{{ roleNames[item.role] || item.role }}</td>
              <td>
                <span class="tag" :class="item.enabled ? 'green' : 'amber'">{{
                  item.enabled ? "已启用" : "已停用"
                }}</span>
              </td>
            <td><button class="secondary" @click="openEdit(item)">编辑</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <p class="page-footnote">
      汇报关系：普通员工 → 组长 → 部门经理 → 总经理 → 董事长，可按实际情况跨级汇报。职级与权限角色独立设置，审批流尚未自动接入此层级。
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
          <h2 id="create-user-title">{{ editing ? "编辑员工档案" : "创建一个新账号" }}</h2>
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
            :disabled="saving || editing"
        /></label>
        <label>姓名<input v-model="displayName" maxlength="100" :disabled="saving" placeholder="员工姓名" /></label>
        <label>所属部门<select v-model="departmentId" :disabled="saving">
          <option :value="null">未分配</option>
          <option v-for="dep in organization.departments" :key="dep.id" :value="dep.id">{{ dep.name }}</option>
        </select></label>
        <div class="row-actions">
          <input v-model="newDepartment" aria-label="新部门名称" placeholder="新部门名称" maxlength="50" :disabled="saving" />
          <button class="secondary" type="button" :disabled="saving || !newDepartment.trim()" @click="addDepartment">新增部门</button>
        </div>
        <label>岗位职级<select v-model="jobLevel" :disabled="saving">
          <option v-for="level in organization.job_levels" :key="level.value" :value="level.value">{{ level.label }}</option>
        </select></label>
        <label>直属上级<select v-model="managerUsername" :disabled="saving">
          <option :value="null">未分配 / 无上级</option>
          <option v-if="managerUsername && !managers.some((item) => item.username === managerUsername)" :value="managerUsername" disabled>当前上级不符合新组织关系，请重新选择</option>
          <option v-for="item in managers" :key="item.username" :value="item.username">{{ item.display_name || item.username }} · {{ levelNames[item.job_level] }} ({{ item.username }})</option>
        </select></label>
        <label
          >权限角色<select v-model="role" :disabled="saving">
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
        <label v-if="!editing"
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
        <label v-if="!editing"
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
            {{ saving ? "正在保存…" : editing ? "保存修改" : "创建账号" }}
          </button>
        </div>
      </form>
    </div>
  </section>
</template>

<style scoped>
.account-name { display: block; color: var(--muted); margin-top: 4px; }
.row-actions select { max-width: 150px; }
td { white-space: nowrap; }
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
  color: var(--muted);
  background: var(--surface);
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
  color: var(--muted);
  margin-top: 4px;
}
.user-create-modal .notice {
  margin-top: 16px;
}
</style>
