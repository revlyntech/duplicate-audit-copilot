import {
  hubspot, Button, Text, Divider, Flex, Tile, Tag,
  Alert, LoadingSpinner, Heading, Statistics, StatisticsItem,
} from "@hubspot/ui-extensions";
import { useState } from "react";

// Update this when your ngrok URL changes
const BACKEND_URL = "https://barista-giving-thinly.ngrok-free.dev";

hubspot.extend(({ context, actions }: any) => (
  <DuplicateAuditCard context={context} actions={actions} />
));

const DuplicateAuditCard = ({ context, actions }: any) => {
  const [status, setStatus]         = useState("idle");
  const [result, setResult]         = useState<any>(null);
  const [error, setError]           = useState("");
  const [pollCount, setPollCount]   = useState(0);
  const [dismissed, setDismissed]   = useState<string[]>([]);
  const [merging, setMerging]       = useState<string | null>(null);

  // Tracks which record the user picked as master per cluster
  // Key = clusterKey (ids joined), Value = record id chosen as master
  const [userMaster, setUserMaster] = useState<Record<string, string>>({});

  const contactId = String(context?.crm?.objectId || "unknown");

  // ── Start audit ───────────────────────────────────────────
  const runAudit = async () => {
    setStatus("starting");
    setError("");
    setResult(null);
    setDismissed([]);
    setUserMaster({});
    try {
      const res = await hubspot.fetch(`${BACKEND_URL}/start-job`, {
        method: "POST",
        body: JSON.stringify({ records: [{ id: contactId }] }) as any,
      });
      const data = await res.json();
      if (data.error) { setError(data.error); setStatus("error"); return; }
      setStatus("polling");
      pollForResult(data.job_id, 0);
    } catch (e: any) { setError(e.message); setStatus("error"); }
  };

  // ── Poll for result ────────────────────────────────────────
  const pollForResult = async (id: string, attempt: number) => {
    if (attempt > 20) {
      setError("Timed out. Is worker.py running?");
      setStatus("error"); return;
    }
    setPollCount(attempt + 1);
    try {
      const res = await hubspot.fetch(`${BACKEND_URL}/job/${id}`);
      const data = await res.json();
      if (data.status === "done")  { setResult(data.result); setStatus("done"); return; }
      if (data.status === "error") { setError(data.message || "Worker failed"); setStatus("error"); return; }
      setTimeout(() => pollForResult(id, attempt + 1), 2500);
    } catch (e: any) { setError(e.message); setStatus("error"); }
  };

  // ── Merge two records ──────────────────────────────────────
  const handleMerge = async (
    primaryId: string,
    duplicateId: string,
    primaryName: string,
    duplicateName: string
  ) => {
    setMerging(duplicateId);
    try {
      const res = await hubspot.fetch(`${BACKEND_URL}/merge`, {
        method: "POST",
        body: JSON.stringify({
          primary_id:   primaryId,
          duplicate_id: duplicateId,
        }) as any,
      });
      const data = await res.json();
      if (data.ok) {
        actions.addAlert({
          type: "success",
          message: `"${duplicateName}" merged into "${primaryName}". Re-scanning...`,
        });
        runAudit();
      } else {
        actions.addAlert({
          type: "danger",
          message: `Merge failed: ${data.error || "Unknown error"}`,
        });
      }
    } catch (e: any) {
      actions.addAlert({ type: "danger", message: e.message });
    }
    setMerging(null);
  };

  const visibleClusters = result?.clusters?.filter(
    (c: any) => !dismissed.includes(c.cluster_ids.join(","))
  ) || [];

  // ── Render ────────────────────────────────────────────────
  return (
    <Flex direction="column" gap="md">
      <Heading>Duplicate Audit Copilot</Heading>
      <Divider />

      {/* IDLE */}
      {status === "idle" && (
        <Flex direction="column" gap="sm">
          <Text>
            Scans all contacts and finds duplicates using email,
            phone, and name matching. You choose which record to keep.
          </Text>
          <Button onClick={runAudit} variant="primary">
            Run Duplicate Audit
          </Button>
        </Flex>
      )}

      {/* LOADING */}
      {(status === "starting" || status === "polling") && (
        <Flex direction="column" align="center" gap="sm">
          <LoadingSpinner
            label={
              status === "starting"
                ? "Starting audit..."
                : `Scanning contacts... (${pollCount}/20)`
            }
          />
        </Flex>
      )}

      {/* ERROR */}
      {status === "error" && (
        <Flex direction="column" gap="sm">
          <Alert title="Audit failed" variant="error">{error}</Alert>
          <Button onClick={runAudit} variant="secondary">Try Again</Button>
        </Flex>
      )}

      {/* DONE */}
      {status === "done" && result && (
        <Flex direction="column" gap="md">

          {/* Summary */}
          <Statistics>
            <StatisticsItem
              label="Duplicate groups found"
              number={visibleClusters.length}
            />
          </Statistics>
          {result.total_records_scanned && (
            <Text>{result.total_records_scanned} total contacts scanned</Text>
          )}
          <Divider />

          {/* Clean CRM */}
          {visibleClusters.length === 0 && (
            <Flex direction="column" align="center" gap="sm">
              <Text format={{ fontWeight: "bold" }}>
                No duplicates found — your CRM is clean!
              </Text>
              <Button onClick={runAudit} variant="secondary" size="xs">
                Re-run Audit
              </Button>
            </Flex>
          )}

          {/* Each duplicate group */}
          {visibleClusters.map((cluster: any, idx: number) => {
            const conf       = cluster.confidence || 0;
            const variant    = conf >= 0.9 ? "error" : conf >= 0.75 ? "warning" : "default";
            const clusterKey = cluster.cluster_ids.join(",");

            // Which record is master — user pick OR system default
            const masterId = userMaster[clusterKey] || cluster.master_id;
            const masterRecord = cluster.records?.find((r: any) => r.id === masterId);

            return (
              <Tile key={idx}>
                <Flex direction="column" gap="sm">

                  {/* Group header */}
                  <Flex justify="between" align="center">
                    <Text format={{ fontWeight: "bold" }}>
                      Duplicate Group #{idx + 1}
                    </Text>
                    <Tag variant={variant}>
                      {(conf * 100).toFixed(0)}% match
                    </Tag>
                  </Flex>

                  {/* Why flagged */}
                  {cluster.reasons?.length > 0 && (
                    <Flex direction="column" gap="xs">
                      {cluster.reasons.map((reason: string, i: number) => (
                        <Text key={i}>• {reason}</Text>
                      ))}
                    </Flex>
                  )}

                  <Divider />

                  {/* Instruction */}
                  <Text format={{ italic: true }}>
                    Select which record to keep, then merge the duplicate into it.
                  </Text>

                  {/* Records */}
                  {cluster.records?.map((rec: any, recIdx: number) => {
                    const isCurrentMaster = rec.id === masterId;
                    return (
                      <Flex key={rec.id} direction="column" gap="xs">

                        {/* Record header */}
                        <Flex justify="between" align="center">
                          <Text format={{ fontWeight: "demibold" }}>
                            {rec.name || "Unknown Name"}
                          </Text>
                          <Tag variant={isCurrentMaster ? "success" : "default"}>
                            {isCurrentMaster ? "Will Keep" : "Will Remove"}
                          </Tag>
                        </Flex>

                        {/* Record details */}
                        {rec.email   && <Text>Email: {rec.email}</Text>}
                        {rec.phone   && <Text>Phone: {rec.phone}</Text>}
                        {rec.company && <Text>Company: {rec.company}</Text>}
                        <Text>ID: {rec.id}</Text>

                        {/* Set as master button — only on non-master records */}
                        {!isCurrentMaster && (
                          <Button
                            onClick={() =>
                              setUserMaster(prev => ({
                                ...prev,
                                [clusterKey]: rec.id,
                              }))
                            }
                            variant="secondary"
                            size="xs"
                          >
                            Set as Master (keep this one instead)
                          </Button>
                        )}

                        {/* Merge button — only on non-master records */}
                        {!isCurrentMaster && (
                          <Button
                            onClick={() =>
                              handleMerge(
                                masterId,
                                rec.id,
                                masterRecord?.name || masterId,
                                rec.name || rec.id
                              )
                            }
                            variant="destructive"
                            size="xs"
                            disabled={merging === rec.id}
                          >
                            {merging === rec.id
                              ? "Merging..."
                              : `Merge "${rec.name || rec.id}" into "${masterRecord?.name || masterId}"`}
                          </Button>
                        )}

                        {recIdx < cluster.records.length - 1 && <Divider />}
                      </Flex>
                    );
                  })}

                  <Divider />

                  {/* Dismiss */}
                  <Button
                    onClick={() =>
                      setDismissed(prev => [...prev, clusterKey])
                    }
                    variant="secondary"
                    size="xs"
                  >
                    Not a duplicate — dismiss
                  </Button>

                </Flex>
              </Tile>
            );
          })}

          {visibleClusters.length > 0 && (
            <Button onClick={runAudit} variant="secondary" size="xs">
              Re-run Audit
            </Button>
          )}

        </Flex>
      )}
    </Flex>
  );
};
