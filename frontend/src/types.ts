export interface OrganizationProfile {
  display_name: string;
  department_id: number | null;
  department_name: string | null;
  job_level: string;
  manager_username: string | null;
}
export interface OrganizationOptions {
  departments: { id: number; name: string }[];
  job_levels: { value: string; label: string }[];
}
export interface User extends OrganizationProfile {
  username: string;
  role: string;
  permissions: string[];
}

export interface ManagedUser extends OrganizationProfile {
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
  confirmation: string;
  id: number;
  question: string;
  answer: string;
  domain: string;
  status: "processing" | "completed" | "failed";
  feedback_allowed: boolean;
  rating: string | null;
  created_at: string;
}
export interface AccessGroup {
  department_ids: number[];
  min_job_level: string | null;
  usernames: string[];
}
export interface AccessPolicy {
  mode: "legacy" | "all" | "restricted";
  groups: AccessGroup[];
}
export interface AccessOptions extends OrganizationOptions {
  users: { username: string; display_name: string }[];
}
export interface KnowledgeFile {
  index_status: "pending" | "processing" | "failed" | "ready";
  access_mode: AccessPolicy["mode"];
  can_read: boolean;
  can_manage: boolean;
  id: number;
  title: string;
  scope: string;
  source_format: string | null;
  chunks: number;
  created_at: string;
}
export interface KnowledgeJob {
  id: string;
  document_id: number;
  kind: "import" | "reindex";
  status: "queued" | "running" | "succeeded" | "failed";
  stage: string;
  processed_chunks: number;
  total_chunks: number;
  attempts: number;
  error: string;
  created_at: string;
  updated_at: string;
  can_retry: boolean;
  title?: string;
}
export interface Handoff {
  status: "pending" | "resolved";
  reply: string;
  resolved_at: string | null;
  resolved_by: string | null;
  read_at: string | null;
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

export interface ExpenseStep {
  id: number;
  round: number;
  position: number;
  approver: string;
  status: "pending" | "waiting" | "approved" | "rejected" | "superseded";
  comment: string;
  reviewed_at: string | null;
}
export interface Expense {
  id: number;
  created_by: string;
  amount: string;
  description: string;
  status: "pending" | "approved" | "rejected";
  created_at: string;
  approval_round: number;
  routing_issue: string;
  current_approver: string | null;
  can_review: boolean;
  can_reassign: boolean;
  steps: ExpenseStep[];
}

export interface TicketEvent {
  id: number;
  actor: string;
  action: "claim" | "resolve" | "confirm" | "reopen";
  comment: string;
  created_at: string;
}

export interface Ticket {
  id: number;
  created_by: string;
  description: string;
  status: "open" | "in_progress" | "awaiting_confirmation" | "closed";
  status_label: string;
  created_at: string;
  assigned_to: string | null;
  resolution: string;
  version: number;
  updated_at: string | null;
  can_claim: boolean;
  can_resolve: boolean;
  can_confirm: boolean;
  can_reopen: boolean;
  events?: TicketEvent[];
}
