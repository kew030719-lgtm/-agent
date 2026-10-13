import type { ExpenseStep } from "./types";

export type LeavePeriod = "am" | "pm";

export interface LeaveRequest {
  start_date: string;
  start_period: LeavePeriod;
  end_date: string;
  end_period: LeavePeriod;
  reason: string;
}

export interface LeaveBalance {
  balance_days: number | null;
  reserved_days: number;
  available_days: number | null;
}

export interface LeavePreview extends LeaveRequest {
  days: number;
  calendar_signature: string;
  work_dates: string[];
  calendar_note: string;
  available_days: number | null;
  current_approver: string | null;
  routing_issue: string;
}

export interface Leave extends LeaveRequest {
  id: number;
  created_by: string;
  days: number;
  status: "pending" | "approved" | "rejected" | "cancelled";
  created_at: string;
  version: number;
  approval_round: number;
  routing_issue: string;
  calendar_issue?: string;
  current_approver: string | null;
  can_review: boolean;
  can_cancel: boolean;
  can_reassign: boolean;
  steps: ExpenseStep[];
}

export interface CalendarOverride {
  date: string;
  is_workday: boolean;
  note: string;
}
