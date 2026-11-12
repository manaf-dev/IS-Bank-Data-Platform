import type { VisualizationSpec } from "@microsoft/fabric-visuals";
import type { ColumnMetadataMap } from "@/lib/to-data-table";
import { type DashboardFilters, withFilters } from "@/lib/dashboard-filters";
import { connection } from "../connection";
import baseQuery from "./breaks-by-branch.dax?raw";
import spec from "./breaks-by-branch.json";

export const columnMetadata: ColumnMetadataMap = {
    "dim_branch[branch_name]": { name: "Branch", displayName: "Branch" },
    "[Reconciliation Breaks]": { name: "Breaks", displayName: "Breaks", format: "#,0" },
};

export const vegaLiteSpec = spec as VisualizationSpec;

/** Months where GL fee income and core fee income disagree, by branch. */
export function breaksByBranch(filters: DashboardFilters) {
    return { connection, query: withFilters(baseQuery, filters), columnMetadata, vegaLiteSpec };
}
