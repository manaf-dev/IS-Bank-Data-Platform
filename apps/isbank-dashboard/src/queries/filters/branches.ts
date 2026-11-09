import type { ColumnMetadataMap } from "@/lib/to-data-table";
import { connection } from "../connection";
import query from "./branches.dax?raw";

export const columnMetadata: ColumnMetadataMap = {
    "dim_branch[region]": { name: "Region", displayName: "Region" },
    "dim_branch[branch_name]": { name: "Branch", displayName: "Branch" },
};

/** Regions and their branches, for the organisation filters. */
export function branchOptions() {
    return { connection, query, columnMetadata };
}
