<script setup lang="ts">
import { ref } from "vue";
import { ArrowUpRight, Layers3, ShieldCheck, ArrowRight } from "@lucide/vue";
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
  <div class="login-layout">
    <section class="login-story">
      <div class="brand">
        <span class="brand-mark"><Layers3 :size="24" /></span
        ><strong>栖知<span>WORKSPACE ASSISTANT</span></strong>
      </div>
      <div class="story-copy">
        <span class="eyebrow">LESS SEARCHING. MORE DOING.</span>
        <h1>工作里的问题，<br />在这里找到答案<span>。</span></h1>
        <p>连接公司知识与日常事务，<br />让每一位员工，专注更重要的事。</p>
        <div class="story-tags">
          <span>知识有据可查</span><span>操作遵循权限</span
          ><span>问题有人接手</span>
        </div>
      </div>
      <div class="story-footer">
        <span>你的企业内部员工助手</span><ArrowUpRight :size="20" />
      </div>
    </section>
    <section class="login-form-area">
      <form class="login-form" @submit.prevent="login">
        <span class="eyebrow">WELCOME BACK</span>
        <h2>登录你的工作空间</h2>
        <p class="muted">使用已有员工账号，继续今天的工作。</p>
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
          {{ busy ? "正在登录…" : "进入工作空间" }}<ArrowRight :size="18" />
        </button>
        <p class="login-note">
          <ShieldCheck :size="16" />登录后根据账号角色开放知识与操作权限。
        </p>
        <p class="login-help">还没有账号？请联系管理员创建。</p>
      </form>
    </section>
  </div>
</template>
