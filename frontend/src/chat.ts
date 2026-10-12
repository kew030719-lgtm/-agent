import { ref } from "vue";
import { api, errorText } from "./api";
import type { ChatTurn, Conversation } from "./types";

export const conversations = ref<Conversation[]>([]);
export const selectedId = ref("");
export const turns = ref<ChatTurn[]>([]);
export const busy = ref(false);
export const loading = ref(false);
export const chatError = ref("");
export const hasMoreConversations = ref(false);
export const historyLoading = ref(false);
let historyVersion = 0;
let historyOffset = 0;
const historyPageSize = 100;
let selectionVersion = 0;
let attempted: { conversation: string; question: string; id: string } | null =
  null;

export function resetChat(): void {
  historyVersion++;
  historyOffset = 0;
  hasMoreConversations.value = false;
  historyLoading.value = false;
  selectionVersion++;
  attempted = null;
  conversations.value = [];
  selectedId.value = "";
  turns.value = [];
  chatError.value = "";
}

export async function refreshConversations(): Promise<void> {
  const version = ++historyVersion;
  historyLoading.value = true;
  try {
    const page = await api<Conversation[]>(
      `/conversations?limit=${historyPageSize}&offset=0`,
    );
    if (version !== historyVersion) return;
    conversations.value = page;
    historyOffset = page.length;
    hasMoreConversations.value = page.length === historyPageSize;
  } finally {
    if (version === historyVersion) historyLoading.value = false;
  }
}

export async function loadMoreConversations(): Promise<void> {
  if (historyLoading.value || !hasMoreConversations.value) return;
  const version = historyVersion;
  historyLoading.value = true;
  try {
    const page = await api<Conversation[]>(
      `/conversations?limit=${historyPageSize}&offset=${historyOffset}`,
    );
    if (version !== historyVersion) return;
    const knownIds = new Set(conversations.value.map((item) => item.id));
    conversations.value.push(...page.filter((item) => !knownIds.has(item.id)));
    historyOffset += page.length;
    hasMoreConversations.value = page.length === historyPageSize;
  } catch (error) {
    if (version === historyVersion) chatError.value = errorText(error);
  } finally {
    if (version === historyVersion) historyLoading.value = false;
  }
}

export async function selectConversation(id: string): Promise<void> {
  if (busy.value) return;
  const version = ++selectionVersion;
  selectedId.value = id;
  turns.value = [];
  chatError.value = "";
  loading.value = true;
  try {
    const result = await api<ChatTurn[]>(`/conversations/${id}/messages`);
    if (version === selectionVersion) turns.value = result;
  } catch (error) {
    if (version === selectionVersion) chatError.value = errorText(error);
  } finally {
    if (version === selectionVersion) loading.value = false;
  }
}

export async function newConversation(): Promise<void> {
  if (busy.value) return;
  selectionVersion++;
  selectedId.value = "";
  turns.value = [];
  chatError.value = "";
  loading.value = false;
  attempted = null;
}

export async function sendQuestion(question: string): Promise<boolean> {
  if (busy.value || loading.value || !question.trim()) return false;
  busy.value = true;
  chatError.value = "";
  try {
    if (!selectedId.value) {
      const conversation = await api<Conversation>("/conversations", {
        method: "POST",
      });
      selectedId.value = conversation.id;
    }
    if (
      !attempted ||
      attempted.conversation !== selectedId.value ||
      attempted.question !== question
    ) {
      attempted = {
        conversation: selectedId.value,
        question,
        id: crypto.randomUUID(),
      };
    }
    const result = await api<ChatTurn>(
      `/conversations/${selectedId.value}/messages`,
      {
        method: "POST",
        body: JSON.stringify({ question, request_id: attempted.id }),
      },
    );
    attempted = null;
    if (!turns.value.some((turn) => turn.id === result.id))
      turns.value.push(result);
    try {
      await refreshConversations();
    } catch {
      chatError.value = "回答已完成，但对话列表刷新失败。请刷新页面查看。";
    }
    return true;
  } catch (error) {
    chatError.value = errorText(error);
    if (selectedId.value) {
      try {
        turns.value = await api<ChatTurn[]>(
          `/conversations/${selectedId.value}/messages`,
        );
      } catch {
        /* 保留原始错误 */
      }
    }
    return false;
  } finally {
    busy.value = false;
  }
}
