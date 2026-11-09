import type { VisualizationSpec } from "@microsoft/fabric-visuals";
import type { ColumnMetadataMap } from "@/lib/to-data-table";
import { type DashboardFilters, withFilters } from "@/lib/dashboard-filters";
import { connection } from "../connection";
import baseQuery from "./profit-by-branch.dax?raw";
import spec from "./profit-by-branch.json";

export const columnMetadata: ColumnMetadataMap = {
    "dim_branch[branch_name]": { name: "Branch", displayName: "Branch" },
    "[Net Profit]": { name: "NetProfit", displayName: "Net profit (GHS)", format: "#,0" },
};

export const vegaLiteSpec = spec as VisualizationSpec;

/** Net profit (GL income less expense) by branch. */
export function profitByBranch(filters: DashboardFilters) {
    return { connection, query: withFilters(baseQuery, filters), columnMetadata, vegaLiteSpec };
}
