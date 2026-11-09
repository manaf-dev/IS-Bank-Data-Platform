import type { VisualizationSpec } from "@microsoft/fabric-visuals";
import type { ColumnMetadataMap } from "@/lib/to-data-table";
import { type DashboardFilters, withFilters } from "@/lib/dashboard-filters";
import { connection } from "../connection";
import baseQuery from "./transaction-trend.dax?raw";
import spec from "./transaction-trend.json";

export const columnMetadata: ColumnMetadataMap = {
    "dim_date[year]": { name: "Year", displayName: "Year" },
    "dim_date[month]": { name: "Month", displayName: "Month" },
    "[Transaction Value]": { name: "TransactionValue", displayName: "Transaction value (GHS)", format: "#,0" },
};

export const vegaLiteSpec = spec as VisualizationSpec;

/** Monthly transaction value. */
export function transactionTrend(filters: DashboardFilters) {
    return { connection, query: withFilters(baseQuery, filters), columnMetadata, vegaLiteSpec };
}
