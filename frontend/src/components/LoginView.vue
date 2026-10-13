<script setup lang="ts">
import { ref } from "vue";
import { Building2, ArrowRight } from "@lucide/vue";
import { api, errorText } from "../api";
import type { User } from "../types";

const emit = defineEmits<{ login: [user: User] }>();
const username = ref(""),
  password = ref(""),
  error = ref(""),
  busy = ref(false);
async function login(): Promise<void> {
  busy.value = true;
  error.value = "";
  try {
    const user = await api<User>("/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: username.value,
        password: password.value,
      }),
    });
    password.value = "";
    emit("login", user);
  } catch (err) {
    error.value = errorText(err);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <main class="login-layout">
    <section class="login-panel">
      <div class="brand login-brand">
        <span class="brand-mark"><Building2 :size="22" /></span
        ><strong>栖知<span>员工工作台</span></strong>
      </div>
      <form class="login-form" @submit.prevent="login">
        <h1>账号登录</h1>
        <p class="muted">使用公司分配的员工账号登录。</p>
        <label
          >员工账号<input
            v-model="username"
            name="username"
            autocomplete="username"
            placeholder="请输入账号"
            required
            maxlength="100"
        /></label>
        <label
          >登录密码<input
            v-model="password"
            name="password"
            autocomplete="current-password"
            type="password"
            placeholder="请输入密码"
            required
            maxlength="256"
        /></label>
        <p v-if="error" class="notice error" role="alert">{{ error }}</p>
        <button class="primary login-button" :disabled="busy">
          {{ busy ? "正在登录…" : "登录" }}<ArrowRight :size="17" />
        </button>
        <p class="login-help">账号开通或权限调整，请联系管理员。</p>
      </form>
      <footer class="login-footer">栖知 · 企业内部服务</footer>
    </section>
  </main>
</template>
