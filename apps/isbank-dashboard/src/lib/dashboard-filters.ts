/**
 * Page filters shared by every IS Bank query. They are applied in DAX, not client-side,
 * because measures such as Deposits (latest month-end balance) and Active Customers
 * (distinct count) can't be re-aggregated from pre-grouped rows.
 */
export interface DashboardFilters {
    year: number | null;
    quarter: number | null;
    region: string | null;
    branch: string | null;
}

export const NO_FILTERS: DashboardFilters = { year: null, quarter: null, region: null, branch: null };

/** Escapes a value for a DAX string literal. */
export function daxString(value: string): string {
    return `"${value.replaceAll('"', '""')}"`;
}

/** TREATAS filter arguments for the active filters, against known model columns only. */
export function filterArguments(filters: DashboardFilters): string[] {
    const args: string[] = [];
    if (filters.year !== null) args.push(`TREATAS({${Math.trunc(filters.year)}}, 'dim_date'[year])`);
    if (filters.quarter !== null) args.push(`TREATAS({${Math.trunc(filters.quarter)}}, 'dim_date'[quarter])`);
    if (filters.region !== null) args.push(`TREATAS({${daxString(filters.region)}}, 'dim_branch'[region])`);
    if (filters.branch !== null) args.push(`TREATAS({${daxString(filters.branch)}}, 'dim_branch'[branch_name])`);
    return args;
}

/**
 * Wraps the table expression of a single-EVALUATE query in CALCULATETABLE with the active
 * filters, keeping any ORDER BY outside. The base .dax stays a complete, testable query.
 */
export function withFilters(query: string, filters: DashboardFilters): string {
    const args = filterArguments(filters);
    if (args.length === 0) return query;
    const match = /^\s*EVALUATE\s+([\s\S]*?)(\s+ORDER\s+BY\s[\s\S]*)?$/i.exec(query);
    if (!match) throw new Error("withFilters expects a query of the form EVALUATE <table> [ORDER BY ...]");
    const [, table, orderBy = ""] = match;
    return `EVALUATE\nCALCULATETABLE (\n${table.trim()},\n    ${args.join(",\n    ")}\n)${orderBy}`;
}
