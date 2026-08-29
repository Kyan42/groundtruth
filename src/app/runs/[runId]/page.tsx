import { notFound } from "next/navigation";

import { RunDashboard } from "@/components/run-dashboard";
import { GroundtruthError } from "@/lib/domain/errors";
import { getRunService } from "@/lib/orchestration/run-service";

export const dynamic = "force-dynamic";

export default async function RunPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = await params;
  try {
    const view = await getRunService().getView(runId);
    return <RunDashboard initialView={view} />;
  } catch (error) {
    if (error instanceof GroundtruthError && error.status === 404) {
      notFound();
    }
    throw error;
  }
}
