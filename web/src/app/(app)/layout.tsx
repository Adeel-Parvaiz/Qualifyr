import { AppShell } from "@/components/app-shell";

/** The signed-in application: sidebar, campaign picker, backend status.
 *
 * Phase 2 adds the auth guard here - this layout is the single place every dashboard
 * route passes through, so one redirect covers all of them.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
