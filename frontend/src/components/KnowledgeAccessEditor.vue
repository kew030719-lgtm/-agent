<script setup lang="ts">
import type { AccessPolicy, AccessOptions } from "../types";
const props = defineProps<{ modelValue: AccessPolicy; options: AccessOptions; disabled?: boolean }>();
const emit = defineEmits<{ "update:modelValue": [value: AccessPolicy] }>();
function changeMode(event: Event): void {
  const mode = (event.target as HTMLSelectElement).value as AccessPolicy["mode"];
  emit("update:modelValue", { mode, groups: [] });
}
function addGroup(): void {
  emit("update:modelValue", { ...props.modelValue, groups: [...props.modelValue.groups,
    { department_ids: [], min_job_level: null, usernames: [] }] });
}
function removeGroup(index: number): void {
  emit("update:modelValue", { ...props.modelValue, groups: props.modelValue.groups.filter((_, i) => i !== index) });
}
</script>
<template>
  <div class="access-editor">
    <label>访问规则<select :value="modelValue.mode" :disabled="disabled" @change="changeMode">
      <option value="restricted">自定义授权</option>
      <option value="all">全体员工</option>
      <option value="legacy">沿用原权限角色范围</option>
    </select></label>
    <template v-if="modelValue.mode === 'restricted'">
      <p class="muted">组内条件同时满足，组与组之间满足任意一组即可。部门和员工支持多选（Ctrl / Command）。</p>
      <p v-if="!modelValue.groups.length" class="muted">尚未授权任何人：正文不可阅读，负责人仍可管理授权。</p>
      <fieldset v-for="(group, index) in modelValue.groups" :key="index" :disabled="disabled">
        <legend>授权组 {{ index + 1 }}</legend>
        <label>指定部门（不选则不限部门）<select v-model="group.department_ids" multiple size="4">
          <option v-for="dep in options.departments" :key="dep.id" :value="dep.id">{{ dep.name }}</option>
        </select></label>
        <label>最低职级<select v-model="group.min_job_level">
          <option :value="null">不限职级</option>
          <option v-for="level in options.job_levels" :key="level.value" :value="level.value">{{ level.label }}及以上</option>
        </select></label>
        <label>指定员工（不选则不限员工）<select v-model="group.usernames" multiple size="4">
          <option v-for="person in options.users" :key="person.username" :value="person.username">{{ person.display_name || person.username }} · {{ person.username }}</option>
        </select></label>
        <p v-if="!group.department_ids.length && !group.min_job_level && !group.usernames.length" class="notice error">这一组至少选择一个条件。</p>
        <button class="secondary" type="button" @click="removeGroup(index)">移除这一组</button>
      </fieldset>
      <button class="secondary" type="button" :disabled="disabled || modelValue.groups.length >= 20" @click="addGroup">添加授权组（或者）</button>
    </template>
    <p v-else-if="modelValue.mode === 'all'" class="muted">所有已启用的员工账号都能阅读。</p>
    <p v-else class="muted">按原 general / hr / it / finance 权限判断，与组织部门不同。</p>
  </div>
</template>
<style scoped>
.access-editor { display: grid; gap: 12px; margin: 16px 0; }
.access-editor label { display: grid; gap: 6px; }
.access-editor p { font-size: 12px; line-height: 1.7; margin: 0; }
fieldset { border: 1px solid var(--line); border-radius: 4px; padding: 14px; display: grid; gap: 12px; min-width: 0; }
legend { font-size: 13px; padding: 0 6px; }
select[multiple] { min-height: 100px; }
</style>
