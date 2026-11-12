import type { ColumnMetadataMap } from "@/lib/to-data-table";
import { type DashboardFilters, withFilters } from "@/lib/dashboard-filters";
import { connection } from "../connection";
import baseQuery from "./pnl-by-branch.dax?raw";

/** The serialized rollup flag; toRollupDataTables splits body and grand-total rows on it. */
export const rollupFlag = "[IsGrandTotal]";

export const columnMetadata: ColumnMetadataMap = {
    "dim_branch[branch_name]": { name: "Branch", displayName: "Branch" },
    "[Income]": { name: "Income", displayName: "Income (GHS)", format: "#,0" },
    "[Expense]": { name: "Expense", displayName: "Expense (GHS)", format: "#,0" },
    "[Net Profit]": { name: "NetProfit", displayName: "Net profit (GHS)", format: "#,0" },
    "[Cost-to-Income Ratio]": { name: "CostToIncome", displayName: "Cost-to-income", format: "0.0%" },
    "[Profit Margin]": { name: "Margin", displayName: "Margin", format: "0.0%" },
};

/** GL profit and loss by branch, with a provided grand-total row. */
export function pnlByBranch(filters: DashboardFilters) {
    return { connection, query: withFilters(baseQuery, filters), columnMetadata };
}
