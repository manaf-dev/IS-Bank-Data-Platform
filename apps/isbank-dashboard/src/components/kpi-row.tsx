import { useLatestTable } from "@/hooks/use-latest-table";
import type { DashboardFilters } from "@/lib/dashboard-filters";
import { type KpiKind, formatKpi } from "@/lib/kpi-format";
import { toDataTable } from "@/lib/to-data-table";
import { cn } from "@/lib/utils";
import { executive } from "@/queries";
import { ErrorBanner, Skeleton } from "./states";

/** Cards in display order, by column alias. Notes stay short: they wrap, never truncate. */
const CARDS: { field: string; label: string; kind: KpiKind; note: string }[] = [
    { field: "Customers", label: "Customers", kind: "count", note: "All time; ignores filters" },
    { field: "ActiveCustomers", label: "Active customers", kind: "count", note: "Transacted in period" },
    { field: "Deposits", label: "Deposits", kind: "money", note: "Latest month-end" },
    { field: "LoanPrincipal", label: "Loan book", kind: "money", note: "Ignores period" },
    { field: "NplRatio", label: "NPL ratio", kind: "percent", note: "Arrears and default" },
    { field: "NetProfit", label: "Net profit", kind: "money", note: "Income less expenses" },
];

const GRID = "grid grid-cols-2 gap-grid md:grid-cols-3 xl:grid-cols-6";

export function KpiRow({ filters }: { filters: DashboardFilters }) {
    const config = executive.kpis.executiveKpis(filters);
    const { table, isLoading, error } = useLatestTable(config.connection, config.query);

    if (error) return <ErrorBanner message={error} />;
    if (!table) {
        return (
            <div className={GRID}>
                {CARDS.map((c) => (
                    <Skeleton key={c.field} className="h-32" />
                ))}
            </div>
        );
    }

    const data = toDataTable(table, config.columnMetadata);
    const row = data.rows[0] ?? [];
    return (
        <dl aria-busy={isLoading} className={cn(GRID, "transition-opacity", isLoading && "opacity-60")}>
            {CARDS.map(({ field, label, kind, note }) => {
                const index = data.columns.findIndex((c) => c.name === field);
                const raw = index >= 0 ? row[index] : null;
                const value = formatKpi(typeof raw === "number" ? raw : null, kind);
                const negative = typeof raw === "number" && raw < 0;
                return (
                    <div
                        key={field}
                        className="flex flex-col gap-2 rounded-xl border border-t-4 border-t-gold bg-card px-4 pb-4 pt-3"
                    >
                        <dt className="text-200 font-semibold uppercase tracking-wider text-muted-foreground">{label}</dt>
                        <dd className="flex flex-col gap-1">
                            <span title={value.exact} className="flex items-baseline gap-1.5">
                                {value.unit && <span className="text-400 font-semibold text-muted-foreground">{value.unit}</span>}
                                <span
                                    className={cn(
                                        "font-numeric text-hero-900 font-bold leading-hero-900 tabular-nums",
                                        negative ? "text-destructive" : "text-emphasis-foreground",
                                    )}
                                >
                                    {value.display}
                                </span>
                                <span className="sr-only">({value.exact})</span>
                            </span>
                            <span className="text-200 text-muted-foreground">{note}</span>
                        </dd>
                    </div>
                );
            })}
        </dl>
    );
}
