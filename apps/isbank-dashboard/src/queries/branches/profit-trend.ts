import type { VisualizationSpec } from "@microsoft/fabric-visuals";
import type { ColumnMetadataMap } from "@/lib/to-data-table";
import { type DashboardFilters, withFilters } from "@/lib/dashboard-filters";
import { connection } from "../connection";
import baseQuery from "./profit-trend.dax?raw";
import spec from "./profit-trend.json";

export const columnMetadata: ColumnMetadataMap = {
    "dim_date[year]": { name: "Year", displayName: "Year" },
    "dim_date[month]": { name: "Month", displayName: "Month" },
    "[Net Profit]": { name: "NetProfit", displayName: "Net profit (GHS)", format: "#,0" },
};

export const vegaLiteSpec = spec as VisualizationSpec;

/** Monthly net profit from the general ledger. */
export function profitTrend(filters: DashboardFilters) {
    return { connection, query: withFilters(baseQuery, filters), columnMetadata, vegaLiteSpec };
}
