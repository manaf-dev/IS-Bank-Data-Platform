import { KpiCard } from "@microsoft/fabric-visuals";
import { useThemeContext } from "@/hooks/theme.context";
import { useLatestTable } from "@/hooks/use-latest-table";
import type { DashboardFilters } from "@/lib/dashboard-filters";
import { toDataTable } from "@/lib/to-data-table";
import { executive } from "@/queries";
import { ErrorBanner, Skeleton } from "./states";

/** Cards in display order, by column alias, with a note where a measure ignores some filters. */
const CARDS: { field: string; note?: string }[] = [
    { field: "Customers", note: "All customers; not filtered by period or branch" },
    { field: "ActiveCustomers" },
    { field: "Deposits", note: "Latest month-end balance" },
    { field: "LoanPrincipal", note: "Whole book; not filtered by period" },
    { field: "NplRatio", note: "Arrears and default share of open book" },
    { field: "NetProfit" },
];

export function KpiRow({ filters }: { filters: DashboardFilters }) {
    const { theme } = useThemeContext();
    const config = executive.kpis.executiveKpis(filters);
    const { table, isLoading, error } = useLatestTable(config.connection, config.query);

    if (error) return <ErrorBanner message={error} />;
    if (!table) {
        return (
            <div className="grid grid-cols-2 gap-grid md:grid-cols-3 xl:grid-cols-6">
                {CARDS.map((c) => (
                    <Skeleton key={c.field} className="h-28" />
                ))}
            </div>
        );
    }

    const data = toDataTable(table, config.columnMetadata);
    const row = data.rows[0] ?? [];
    return (
        <div aria-busy={isLoading} className="grid grid-cols-2 gap-grid md:grid-cols-3 xl:grid-cols-6">
            {CARDS.map(({ field, note }) => {
                const index = data.columns.findIndex((c) => c.name === field);
                const column = data.columns[index];
                const raw = index >= 0 ? row[index] : null;
                const value = typeof raw === "number" ? raw : null;
                return (
                    <KpiCard
                        key={field}
                        theme={theme}
                        header={{ title: column?.displayName ?? field, subtitle: note }}
                        callout={{ metric: { value, format: column?.format } }}
                        className="border-t-4 border-t-gold"
                    />
                );
            })}
        </div>
    );
}
