import type { ColumnMetadataMap } from "@/lib/to-data-table";
import { type DashboardFilters, withFilters } from "@/lib/dashboard-filters";
import { connection } from "../connection";
import baseQuery from "./kpis.dax?raw";

export const columnMetadata: ColumnMetadataMap = {
    "[Income]": { name: "Income", displayName: "Income (GHS)", format: "#,0" },
    "[Expense]": { name: "Expense", displayName: "Expense (GHS)", format: "#,0" },
    "[Net Profit]": { name: "NetProfit", displayName: "Net profit (GHS)", format: "#,0" },
    "[Cost-to-Income Ratio]": { name: "CostToIncome", displayName: "Cost-to-income", format: "0.0%" },
    "[Reconciliation Breaks]": { name: "Breaks", displayName: "GL vs core breaks", format: "#,0" },
};

/** Headline GL figures for the Branches page, one row. */
export function branchKpis(filters: DashboardFilters) {
    return { connection, query: withFilters(baseQuery, filters), columnMetadata };
}
