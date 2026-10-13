export type WorkKind = "leave" | "expense" | "ticket" | "handoff";

export interface WorkTarget {
  kind: WorkKind;
  record_id: number;
}

export interface WorkTodo extends WorkTarget {
  key: string;
  title: string;
  summary: string;
  action_label: string;
  created_at: string;
}

export interface WorkNotification extends WorkTarget {
  id: number;
  title: string;
  created_at: string;
  read_at: string | null;
}

export interface WorkSummary {
  todo_count: number;
  unread_count: number;
}
