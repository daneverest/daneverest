/**
 * Rovo action handlers for the Microsoft Fabric integration.
 *
 * Each exported function corresponds to a rovo:action defined in manifest.yml.
 * The Rovo agent invokes these actions and uses the returned data to formulate
 * evidence-based insights for the user.
 *
 * Parameters are passed by the agent in req.payload, inferred from the action
 * description and conversation context.
 */

import {
  getWorkspaces,
  getWorkspaceItems,
  getSemanticModels,
  executeDaxQuery,
  getLakehouses,
  getLakehouseTables,
  getReports,
} from "./fabric-client";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ActionRequest {
  payload: Record<string, unknown>;
  context: {
    cloudId: string;
    accountId?: string;
  };
}

type ActionResult = Record<string, unknown>;

/**
 * Wraps action handlers to return a consistent error format so the agent can
 * relay helpful messages back to the user rather than failing silently.
 */
async function withErrorHandling(
  fn: () => Promise<ActionResult>
): Promise<ActionResult> {
  try {
    return await fn();
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return {
      error: true,
      message,
      hint: message.includes("credentials are not configured")
        ? "Ask your Atlassian administrator to configure the Forge app's " +
          "FABRIC_TENANT_ID, FABRIC_CLIENT_ID and FABRIC_CLIENT_SECRET " +
          "variables, and ensure the Azure service principal is added to " +
          "the relevant Fabric workspaces."
        : "Check that the workspace/dataset IDs are correct and that the " +
          "service principal has been granted access.",
    };
  }
}

// ---------------------------------------------------------------------------
// list-workspaces
// ---------------------------------------------------------------------------

export const listWorkspaces = async (
  _req: ActionRequest
): Promise<ActionResult> =>
  withErrorHandling(async () => {
    const workspaces = await getWorkspaces();
    return {
      count: workspaces.length,
      workspaces: workspaces.map((w) => ({
        id: w.id,
        name: w.displayName,
        description: w.description ?? null,
        type: w.type ?? null,
        capacityId: w.capacityId ?? null,
      })),
      tip:
        workspaces.length > 0
          ? `Use a workspaceId from this list with other actions to explore contents.`
          : "No workspaces found. Ensure the service principal has been added as a member to at least one Fabric workspace.",
    };
  });

// ---------------------------------------------------------------------------
// list-workspace-items
// ---------------------------------------------------------------------------

export const listWorkspaceItems = async (
  req: ActionRequest
): Promise<ActionResult> =>
  withErrorHandling(async () => {
    const workspaceId = req.payload.workspaceId as string | undefined;
    if (!workspaceId) {
      return {
        error: true,
        message: "workspaceId is required. Use list-workspaces first to obtain one.",
      };
    }

    const items = await getWorkspaceItems(workspaceId);

    // Group by type for a structured overview
    const byType: Record<string, Array<{ id: string; name: string }>> = {};
    for (const item of items) {
      const type = item.type ?? "Unknown";
      if (!byType[type]) byType[type] = [];
      byType[type].push({ id: item.id, name: item.displayName });
    }

    return {
      workspaceId,
      totalItems: items.length,
      itemsByType: byType,
    };
  });

// ---------------------------------------------------------------------------
// list-semantic-models
// ---------------------------------------------------------------------------

export const listSemanticModels = async (
  req: ActionRequest
): Promise<ActionResult> =>
  withErrorHandling(async () => {
    const workspaceId = req.payload.workspaceId as string | undefined;
    if (!workspaceId) {
      return {
        error: true,
        message:
          "workspaceId is required. Use list-workspaces first to obtain one.",
      };
    }

    const models = await getSemanticModels(workspaceId);
    return {
      workspaceId,
      count: models.length,
      semanticModels: models.map((m) => ({
        id: m.id,
        name: m.displayName,
        description: m.description ?? null,
      })),
      tip:
        models.length > 0
          ? "Use a semanticModel id as the datasetId in execute-dax-query."
          : "No semantic models found in this workspace.",
    };
  });

// ---------------------------------------------------------------------------
// execute-dax-query
// ---------------------------------------------------------------------------

export const executeDaxQuery = async (
  req: ActionRequest
): Promise<ActionResult> =>
  withErrorHandling(async () => {
    const workspaceId = req.payload.workspaceId as string | undefined;
    const datasetId = req.payload.datasetId as string | undefined;
    const daxQuery = req.payload.daxQuery as string | undefined;

    if (!workspaceId || !datasetId || !daxQuery) {
      return {
        error: true,
        message:
          "workspaceId, datasetId and daxQuery are all required. " +
          "Use list-semantic-models to get workspaceId and datasetId. " +
          "Provide a DAX EVALUATE expression for daxQuery.",
      };
    }

    const result = await executeDaxQuery(workspaceId, datasetId, daxQuery);

    return {
      workspaceId,
      datasetId,
      query: daxQuery,
      columns: result.columns,
      rowCount: result.rowCount,
      rows: result.rows,
      note:
        result.rowCount === 0
          ? "Query returned no rows. Check that the DAX expression is valid and the table/column names match the semantic model."
          : `Returned ${result.rowCount} row(s).`,
    };
  });

// ---------------------------------------------------------------------------
// list-lakehouses
// ---------------------------------------------------------------------------

export const listLakehouses = async (
  req: ActionRequest
): Promise<ActionResult> =>
  withErrorHandling(async () => {
    const workspaceId = req.payload.workspaceId as string | undefined;
    if (!workspaceId) {
      return {
        error: true,
        message:
          "workspaceId is required. Use list-workspaces first to obtain one.",
      };
    }

    const lakehouses = await getLakehouses(workspaceId);
    return {
      workspaceId,
      count: lakehouses.length,
      lakehouses: lakehouses.map((l) => ({
        id: l.id,
        name: l.displayName,
        description: l.description ?? null,
      })),
      tip:
        lakehouses.length > 0
          ? "Use a lakehouse id with list-lakehouse-tables to see available tables."
          : "No lakehouses found in this workspace.",
    };
  });

// ---------------------------------------------------------------------------
// list-lakehouse-tables
// ---------------------------------------------------------------------------

export const listLakehouseTables = async (
  req: ActionRequest
): Promise<ActionResult> =>
  withErrorHandling(async () => {
    const workspaceId = req.payload.workspaceId as string | undefined;
    const lakehouseId = req.payload.lakehouseId as string | undefined;

    if (!workspaceId || !lakehouseId) {
      return {
        error: true,
        message:
          "workspaceId and lakehouseId are both required. " +
          "Use list-lakehouses to obtain a lakehouseId.",
      };
    }

    const tables = await getLakehouseTables(workspaceId, lakehouseId);
    return {
      workspaceId,
      lakehouseId,
      count: tables.length,
      tables: tables.map((t) => ({
        name: t.name,
        type: t.type,
        format: t.format,
        location: t.location ?? null,
      })),
    };
  });

// ---------------------------------------------------------------------------
// list-reports
// ---------------------------------------------------------------------------

export const listReports = async (
  req: ActionRequest
): Promise<ActionResult> =>
  withErrorHandling(async () => {
    const workspaceId = req.payload.workspaceId as string | undefined;
    if (!workspaceId) {
      return {
        error: true,
        message:
          "workspaceId is required. Use list-workspaces first to obtain one.",
      };
    }

    const reports = await getReports(workspaceId);
    return {
      workspaceId,
      count: reports.length,
      reports: reports.map((r) => ({
        id: r.id,
        name: r.name,
        datasetId: r.datasetId ?? null,
        webUrl: r.webUrl ?? null,
      })),
    };
  });
