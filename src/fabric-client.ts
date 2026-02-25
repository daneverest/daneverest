/**
 * Microsoft Fabric & Power BI API client for Forge.
 *
 * Authentication uses the OAuth2 client-credentials (service principal) flow.
 * Configure the following Forge environment variables before deploying:
 *
 *   forge variables set FABRIC_TENANT_ID   "<your-azure-tenant-id>"
 *   forge variables set FABRIC_CLIENT_ID   "<your-app-client-id>"
 *   forge variables set --encrypt FABRIC_CLIENT_SECRET "<your-client-secret>"
 *
 * The service principal must be added to each Fabric workspace as a member
 * (Contributor role or above) and the Fabric admin must enable
 * "Allow service principals to use Fabric APIs" in the Admin portal.
 */

import { fetch } from "@forge/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Workspace {
  id: string;
  displayName: string;
  description?: string;
  type?: string;
  capacityId?: string;
}

export interface FabricItem {
  id: string;
  displayName: string;
  type: string;
  description?: string;
  workspaceId: string;
}

export interface LakehouseTable {
  name: string;
  type: string;
  format: string;
  location?: string;
  properties?: Record<string, string>;
}

export interface PowerBIReport {
  id: string;
  name: string;
  datasetId?: string;
  datasetWorkspaceId?: string;
  webUrl?: string;
  embedUrl?: string;
}

export interface DaxQueryResult {
  columns: string[];
  rows: Array<Record<string, unknown>>;
  rowCount: number;
}

interface TokenResponse {
  access_token: string;
  expires_in: number;
  token_type: string;
}

interface PaginatedFabricResponse<T> {
  value: T[];
  continuationToken?: string;
}

// ---------------------------------------------------------------------------
// Token acquisition (client credentials / service principal)
// ---------------------------------------------------------------------------

async function getAccessToken(): Promise<string> {
  const tenantId = process.env.FABRIC_TENANT_ID;
  const clientId = process.env.FABRIC_CLIENT_ID;
  const clientSecret = process.env.FABRIC_CLIENT_SECRET;

  if (!tenantId || !clientId || !clientSecret) {
    throw new Error(
      "Microsoft Fabric credentials are not configured. " +
        "Please ask your Forge app administrator to set the " +
        "FABRIC_TENANT_ID, FABRIC_CLIENT_ID and FABRIC_CLIENT_SECRET " +
        "environment variables."
    );
  }

  const tokenUrl = `https://login.microsoftonline.com/${tenantId}/oauth2/v2.0/token`;

  const body = [
    `grant_type=client_credentials`,
    `client_id=${encodeURIComponent(clientId)}`,
    `client_secret=${encodeURIComponent(clientSecret)}`,
    `scope=${encodeURIComponent(
      "https://analysis.windows.net/powerbi/api/.default"
    )}`,
  ].join("&");

  const response = await fetch(tokenUrl, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(
      `Microsoft authentication failed (${response.status}): ${text}`
    );
  }

  const data = (await response.json()) as TokenResponse;
  return data.access_token;
}

// ---------------------------------------------------------------------------
// Low-level HTTP helpers
// ---------------------------------------------------------------------------

async function fabricGet<T>(path: string): Promise<T> {
  const token = await getAccessToken();
  const url = `https://api.fabric.microsoft.com/v1${path}`;

  const response = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Fabric API error (${response.status}) at ${path}: ${text}`);
  }

  return response.json() as Promise<T>;
}

async function powerbiGet<T>(path: string): Promise<T> {
  const token = await getAccessToken();
  const url = `https://api.powerbi.com/v1.0/myorg${path}`;

  const response = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(
      `Power BI API error (${response.status}) at ${path}: ${text}`
    );
  }

  return response.json() as Promise<T>;
}

async function powerbiPost<T>(path: string, body: unknown): Promise<T> {
  const token = await getAccessToken();
  const url = `https://api.powerbi.com/v1.0/myorg${path}`;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(
      `Power BI API error (${response.status}) at ${path}: ${text}`
    );
  }

  return response.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Workspaces
// ---------------------------------------------------------------------------

export async function getWorkspaces(): Promise<Workspace[]> {
  const data = await fabricGet<PaginatedFabricResponse<Workspace>>(
    "/workspaces"
  );
  return data.value ?? [];
}

// ---------------------------------------------------------------------------
// Workspace items
// ---------------------------------------------------------------------------

export async function getWorkspaceItems(
  workspaceId: string
): Promise<FabricItem[]> {
  const data = await fabricGet<PaginatedFabricResponse<FabricItem>>(
    `/workspaces/${workspaceId}/items`
  );
  return data.value ?? [];
}

// ---------------------------------------------------------------------------
// Semantic models (Power BI datasets)
// ---------------------------------------------------------------------------

export async function getSemanticModels(
  workspaceId: string
): Promise<FabricItem[]> {
  const data = await fabricGet<PaginatedFabricResponse<FabricItem>>(
    `/workspaces/${workspaceId}/semanticModels`
  );
  return data.value ?? [];
}

// ---------------------------------------------------------------------------
// DAX queries
// ---------------------------------------------------------------------------

interface ExecuteQueriesResponse {
  results: Array<{
    tables: Array<{
      rows: Array<Record<string, unknown>>;
    }>;
  }>;
}

/**
 * Strips the [TableName] prefix from Power BI column names.
 * e.g. "[Sales].[Total Revenue]" → "Total Revenue"
 *      "Total Revenue"           → "Total Revenue"
 */
function cleanColumnName(raw: string): string {
  // Column names from Power BI come as "[TableName].[ColumnName]" or just
  // "[ColumnName]". Strip the surrounding brackets and any table prefix.
  const match = raw.match(/\[([^\]]+)\]$/);
  return match ? match[1] : raw;
}

export async function executeDaxQuery(
  workspaceId: string,
  datasetId: string,
  daxQuery: string
): Promise<DaxQueryResult> {
  const body = {
    queries: [{ query: daxQuery }],
    serializationSettings: { IncludeNulls: true },
  };

  const data = await powerbiPost<ExecuteQueriesResponse>(
    `/groups/${workspaceId}/datasets/${datasetId}/executeQueries`,
    body
  );

  const firstTable = data.results?.[0]?.tables?.[0];
  if (!firstTable) {
    return { columns: [], rows: [], rowCount: 0 };
  }

  const rawRows = firstTable.rows ?? [];
  if (rawRows.length === 0) {
    return { columns: [], rows: [], rowCount: 0 };
  }

  // Clean up column names and rebuild rows
  const rawColumns = Object.keys(rawRows[0]);
  const columns = rawColumns.map(cleanColumnName);

  const rows = rawRows.map((rawRow) => {
    const cleaned: Record<string, unknown> = {};
    rawColumns.forEach((raw, idx) => {
      cleaned[columns[idx]] = rawRow[raw];
    });
    return cleaned;
  });

  return { columns, rows, rowCount: rows.length };
}

// ---------------------------------------------------------------------------
// Lakehouses
// ---------------------------------------------------------------------------

export async function getLakehouses(
  workspaceId: string
): Promise<FabricItem[]> {
  const data = await fabricGet<PaginatedFabricResponse<FabricItem>>(
    `/workspaces/${workspaceId}/lakehouses`
  );
  return data.value ?? [];
}

// ---------------------------------------------------------------------------
// Lakehouse tables
// ---------------------------------------------------------------------------

interface LakehouseTablesResponse {
  data: LakehouseTable[];
  continuationToken?: string;
}

export async function getLakehouseTables(
  workspaceId: string,
  lakehouseId: string
): Promise<LakehouseTable[]> {
  const data = await fabricGet<LakehouseTablesResponse>(
    `/workspaces/${workspaceId}/lakehouses/${lakehouseId}/tables`
  );
  return data.data ?? [];
}

// ---------------------------------------------------------------------------
// Power BI Reports
// ---------------------------------------------------------------------------

interface PowerBIReportsResponse {
  value: PowerBIReport[];
}

export async function getReports(
  workspaceId: string
): Promise<PowerBIReport[]> {
  const data = await powerbiGet<PowerBIReportsResponse>(
    `/groups/${workspaceId}/reports`
  );
  return data.value ?? [];
}
