import { useLatestTable } from "@/hooks/use-latest-table";
import { type KpiKind, formatKpi } from "@/lib/kpi-format";
import { type ColumnMetadataMap, toDataTable } from "@/lib/to-data-table";
import { cn } from "@/lib/utils";
import { ErrorBanner, Skeleton } from "./states";

export interface KpiCardDef {
    /** Column alias in the query's columnMetadata. */
    field: string;
    label: string;
    kind: KpiKind;
    /** Short context line; it wraps, so keep it to a few words. */
    note: string;
}

interface KpiRowProps {
    config: { connection: string; query: string; columnMetadata: ColumnMetadataMap };
    cards: KpiCardDef[];
}

const GRID: Record<number, string> = {
    5: "grid grid-cols-2 gap-grid md:grid-cols-3 xl:grid-cols-5",
    6: "grid grid-cols-2 gap-grid md:grid-cols-3 xl:grid-cols-6",
};

/** A row of headline figures from a single-row KPI query. */
export function KpiRow({ config, cards }: KpiRowProps) {
    const { table, isLoading, error } = useLatestTable(config.connection, config.query);
    const grid = GRID[cards.length] ?? GRID[6];

    if (error) return <ErrorBanner message={error} />;
    if (!table) {
        return (
            <div className={grid}>
                {cards.map((c) => (
                    <Skeleton key={c.field} className="h-32" />
                ))}
            </div>
        );
    }

    const data = toDataTable(table, config.columnMetadata);
    const row = data.rows[0] ?? [];
    return (
        <dl aria-busy={isLoading} className={cn(grid, "transition-opacity", isLoading && "opacity-60")}>
            {cards.map(({ field, label, kind, note }) => {
                const index = data.columns.findIndex((c) => c.name === field);
                const raw = index >= 0 ? row[index] : null;
                const value = formatKpi(typeof raw === "number" ? raw : null, kind);
                const negative = typeof raw === "number" && raw < 0;
                return (
                    <div
                        key={field}
                        className="flex flex-col gap-1 rounded-xl border border-t-4 border-t-gold bg-card px-4 pb-3 pt-3"
                    >
                        <dt className="text-200 font-semibold uppercase tracking-wider text-muted-foreground">{label}</dt>
                        <dd className="flex flex-col">
                            <span title={value.exact} className="flex items-baseline gap-1.5">
                                {value.unit && <span className="text-400 font-semibold text-muted-foreground">{value.unit}</span>}
                                <span
                                    className={cn(
                                        // Explicit length syntax: inside cn(), tailwind-merge would drop text-hero-900 as
                                        // conflicting with the text-* colour class.
                                        "font-numeric text-[length:var(--text-hero-900)] font-bold leading-hero-900 tabular-nums",
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
