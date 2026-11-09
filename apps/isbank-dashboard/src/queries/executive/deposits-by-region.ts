import type { VisualizationSpec } from "@microsoft/fabric-visuals";
import type { ColumnMetadataMap } from "@/lib/to-data-table";
import { type DashboardFilters, withFilters } from "@/lib/dashboard-filters";
import { connection } from "../connection";
import baseQuery from "./deposits-by-region.dax?raw";
import spec from "./deposits-by-region.json";

export const columnMetadata: ColumnMetadataMap = {
    "dim_branch[region]": { name: "Region", displayName: "Region" },
    "[Deposits]": { name: "Deposits", displayName: "Deposits (GHS)", format: "#,0" },
};

export const vegaLiteSpec = spec as VisualizationSpec;

/** Latest month-end deposits by region. */
export function depositsByRegion(filters: DashboardFilters) {
    return { connection, query: withFilters(baseQuery, filters), columnMetadata, vegaLiteSpec };
}
