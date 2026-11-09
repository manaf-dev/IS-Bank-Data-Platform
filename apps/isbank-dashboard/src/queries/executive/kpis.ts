import type { ColumnMetadataMap } from "@/lib/to-data-table";
import { type DashboardFilters, withFilters } from "@/lib/dashboard-filters";
import { connection } from "../connection";
import baseQuery from "./kpis.dax?raw";

export const columnMetadata: ColumnMetadataMap = {
    "[Customers]": { name: "Customers", displayName: "Customers", format: "#,0" },
    "[Active Customers]": { name: "ActiveCustomers", displayName: "Active customers", format: "#,0" },
    "[Deposits]": { name: "Deposits", displayName: "Deposits (GHS)", format: "#,0" },
    "[Loan Principal]": { name: "LoanPrincipal", displayName: "Loan book (GHS)", format: "#,0" },
    "[NPL Ratio]": { name: "NplRatio", displayName: "NPL ratio", format: "0.0%" },
    "[Net Profit]": { name: "NetProfit", displayName: "Net profit (GHS)", format: "#,0" },
};

/** Headline KPIs for the Executive page, one row. */
export function executiveKpis(filters: DashboardFilters) {
    return { connection, query: withFilters(baseQuery, filters), columnMetadata };
}
