# MS Fabric Rovo Integration

A [Atlassian Forge](https://developer.atlassian.com/platform/forge/) app that brings Microsoft Fabric data directly into [Rovo](https://www.atlassian.com/rovo). The Rovo agent can explore workspaces, query semantic models with DAX, inspect data lakehouses and surface published Power BI reports — enabling evidence-based insights grounded in your organisation's actual data.

---

## What It Does

| Capability | Description |
|---|---|
| **Workspace discovery** | Lists all Fabric workspaces the integration has access to |
| **Content inventory** | Enumerates every item in a workspace (datasets, reports, lakehouses, notebooks, pipelines) |
| **Semantic model queries** | Runs DAX queries against Power BI semantic models and returns live results |
| **Lakehouse exploration** | Lists lakehouses and their delta/managed tables |
| **Report surfacing** | Lists published Power BI reports with direct web links |

The Rovo agent combines these capabilities to answer natural language data questions, spot trends, flag anomalies and make data-backed recommendations in the flow of Jira and Confluence work.

---

## Architecture

```
Rovo (chat)
    └── rovo:agent  (MS Fabric Analyst)
            └── rovo:action × 7
                    └── src/index.ts  (Forge functions)
                            └── src/fabric-client.ts
                                    ├── GET  api.fabric.microsoft.com/v1/workspaces
                                    ├── GET  api.fabric.microsoft.com/v1/workspaces/{id}/items
                                    ├── GET  api.fabric.microsoft.com/v1/workspaces/{id}/semanticModels
                                    ├── GET  api.fabric.microsoft.com/v1/workspaces/{id}/lakehouses
                                    ├── GET  api.fabric.microsoft.com/v1/workspaces/{id}/lakehouses/{id}/tables
                                    ├── GET  api.powerbi.com/v1.0/myorg/groups/{id}/reports
                                    └── POST api.powerbi.com/v1.0/myorg/groups/{id}/datasets/{id}/executeQueries
```

Authentication uses the **OAuth2 client credentials (service principal)** flow — an admin configures the integration once and all Rovo users benefit immediately.

---

## Prerequisites

1. **Atlassian account** with Rovo enabled and Forge CLI installed
2. **Microsoft Azure AD app registration** with:
   - Application (client) ID
   - Client secret
   - API permission: `Power BI Service` — `Dataset.Read.All` and `Workspace.Read.All` (application type)
3. **Fabric admin portal setting**: _Allow service principals to use Fabric APIs_ must be enabled
4. **Service principal added as workspace member**: In each Fabric workspace you want Rovo to access, add the service principal as a Contributor (or higher)

---

## Setup

### 1. Clone and install dependencies

```bash
git clone <this-repo>
cd daneverest
npm install
```

### 2. Register the Forge app

If this is a fresh deployment, register the app to get an app ID:

```bash
forge register
```

This updates the `app.id` in `manifest.yml`. Commit the change.

### 3. Set environment variables

```bash
forge variables set FABRIC_TENANT_ID   "<your-azure-tenant-id>"
forge variables set FABRIC_CLIENT_ID   "<your-azure-app-client-id>"
forge variables set --encrypt FABRIC_CLIENT_SECRET "<your-azure-client-secret>"
```

Use `--environment staging` or `--environment production` flags as needed.

### 4. Deploy

```bash
# Deploy to development environment
forge deploy

# Deploy to staging/production
forge deploy --environment staging
forge deploy --environment production
```

### 5. Install on your Atlassian site

```bash
forge install
```

Select your Atlassian cloud site when prompted. Choose **Rovo** as the product.

---

## Usage

Once installed, open Rovo in Jira or Confluence and start a conversation with the **Microsoft Fabric Analyst** agent.

**Example prompts:**

- _"What workspaces do I have access to in Microsoft Fabric?"_
- _"Show me all the datasets and reports in the Operations workspace."_
- _"Query the sales semantic model and give me monthly revenue for 2024."_
- _"What tables are in the data lake? I need to understand what operational data we have."_
- _"Are there any anomalies in the Q3 sales figures?"_
- _"Find the relevant report and share a link."_

The agent will discover the right workspace, query the data and respond with evidence-backed findings.

---

## Azure AD App Registration — Quick Reference

1. Go to **Azure Portal → Azure Active Directory → App registrations**
2. Click **New registration** — give it a name (e.g. _Rovo Fabric Integration_)
3. Record the **Application (client) ID** and **Directory (tenant) ID**
4. Go to **Certificates & secrets → New client secret** — copy the secret value
5. Go to **API permissions → Add a permission → Power BI Service**
   - Add `Dataset.Read.All` (application permission)
   - Add `Workspace.Read.All` (application permission)
   - Click **Grant admin consent**
6. In the **Fabric Admin Portal**, enable **Allow service principals to use Fabric APIs**
7. In each target workspace, go to **Workspace settings → Access** and add the service principal as **Contributor**

---

## DAX Query Examples

The Rovo agent writes DAX on your behalf. If you want to request specific queries, use natural language and the agent will translate them. Example patterns the agent uses:

```dax
-- Monthly revenue trend
EVALUATE
SUMMARIZECOLUMNS(
    Sales[Month],
    "Revenue", SUM(Sales[Amount])
)
ORDER BY Sales[Month] ASC

-- Top 10 products by margin
EVALUATE
TOPN(10,
    SUMMARIZECOLUMNS(
        Products[ProductName],
        "Margin", AVERAGE(Sales[Margin])
    ),
    [Margin], DESC
)
```

---

## Environment Variables

| Variable | Description | Sensitive |
|---|---|---|
| `FABRIC_TENANT_ID` | Azure AD tenant (directory) ID | No |
| `FABRIC_CLIENT_ID` | Azure app (client) ID | No |
| `FABRIC_CLIENT_SECRET` | Azure app client secret | **Yes** — use `--encrypt` |

---

## Project Structure

```
manifest.yml                  — Forge app manifest (agent, actions, remotes)
package.json
tsconfig.json
src/
  index.ts                    — Rovo action handlers (7 actions)
  fabric-client.ts            — MS Fabric & Power BI REST API client
resources/
  prompts/
    agent-prompt.txt          — Rovo agent system prompt
```

---

## Limitations

- **Row-Level Security (RLS)**: Service principals cannot access datasets that use RLS or Single Sign-On (SSO). Use per-user OAuth2 for those scenarios.
- **Query rate limit**: The Power BI execute queries API allows 120 requests/minute per service principal.
- **Result size**: DAX queries return a maximum of 100,000 rows or 1,000,000 values (whichever is hit first).
- **OneLake direct file access**: This integration queries via the Fabric REST API. Direct file-level OneLake access (ADLS Gen2 compatible) is not included.
