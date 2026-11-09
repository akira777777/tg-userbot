import "server-only";

import { createAdminClient } from "@supabase/server/core";

export function supabaseAdmin() {
  try {
    return createAdminClient();
  } catch (error) {
    const message = error instanceof Error ? error.message : "";
    if (message.includes("SUPABASE_URL") || message.includes("secret")) {
      throw new Error("Cloud mirror is not configured");
    }
    throw error;
  }
}
