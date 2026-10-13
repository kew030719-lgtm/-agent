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
