import type { ColumnMetadataMap } from "@/lib/to-data-table";
import { connection } from "../connection";
import query from "./periods.dax?raw";

export const columnMetadata: ColumnMetadataMap = {
    "dim_date[year]": { name: "Year", displayName: "Year" },
    "dim_date[quarter]": { name: "Quarter", displayName: "Quarter number" },
    "dim_date[quarter_name]": { name: "QuarterName", displayName: "Quarter" },
    "[Transactions]": { name: "Transactions", displayName: "Transactions", format: "#,0" },
};

/** Years and quarters that have transactions, for the period filters. */
export function periodOptions() {
    return { connection, query, columnMetadata };
}
