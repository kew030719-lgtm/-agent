export interface User {
  username: string;
  role: string;
  permissions: string[];
}

export interface ManagedUser {
  username: string;
  role: string;
  enabled: boolean;
}

export interface RoleOption {
  role: string;
  permissions: string[];
}
export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}
export interface ChatTurn {
  id: number;
  question: string;
  answer: string;
  domain: string;
  status: "processing" | "completed" | "failed";
  feedback_allowed: boolean;
  rating: string | null;
  created_at: string;
}
export interface KnowledgeFile {
  id: number;
  title: string;
  scope: string;
  source_format: string | null;
  chunks: number;
  created_at: string;
}
export interface Handoff {
  id: number;
  created_by: string;
  question: string;
  reason: string;
  created_at: string;
}
export interface Feedback {
  id: number;
  username: string;
  question: string;
  answer: string;
  rating: string;
  created_at: string;
}
export interface Usage {
  outcome: string;
  count: number;
  average_ms: number;
}
