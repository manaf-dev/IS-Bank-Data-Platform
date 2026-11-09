import type { VisualizationSpec } from "@microsoft/fabric-visuals";
import type { ColumnMetadataMap } from "@/lib/to-data-table";
import { type DashboardFilters, withFilters } from "@/lib/dashboard-filters";
import { connection } from "../connection";
import baseQuery from "./transactions-by-channel.dax?raw";
import spec from "./transactions-by-channel.json";

export const columnMetadata: ColumnMetadataMap = {
    "dim_channel[channel]": { name: "Channel", displayName: "Channel" },
    "[Transactions]": { name: "Transactions", displayName: "Transactions", format: "#,0" },
};

export const vegaLiteSpec = spec as VisualizationSpec;

/** Transaction count by channel. */
export function transactionsByChannel(filters: DashboardFilters) {
    return { connection, query: withFilters(baseQuery, filters), columnMetadata, vegaLiteSpec };
}
