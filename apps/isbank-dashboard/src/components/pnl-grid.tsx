import { DataGrid } from "@microsoft/fabric-datagrid";
import type { InteractionEvent } from "@microsoft/fabric-visuals-core";
import { useThemeContext } from "@/hooks/theme.context";
import { useLatestTable } from "@/hooks/use-latest-table";
import type { DashboardFilters } from "@/lib/dashboard-filters";
import { selectedValue } from "@/lib/selection";
import { toRollupDataTables } from "@/lib/to-data-table";
import { cn } from "@/lib/utils";
import { branches } from "@/queries";
import { EmptyMessage, ErrorBanner, Skeleton } from "./states";

interface PnlGridProps {
    filters: DashboardFilters;
    onSelectBranch: (branch: string | null) => void;
    className?: string;
}

/** Branch P&L with the grand total supplied by the query, not summed in the browser. */
export function PnlGrid({ filters, onSelectBranch, className }: PnlGridProps) {
    const { theme } = useThemeContext();
    const config = branches.pnlByBranch.pnlByBranch(filters);
    const { table, isLoading, error } = useLatestTable(config.connection, config.query);

    const handleInteraction = (events: InteractionEvent[]) => {
        const branch = selectedValue(events, "Branch");
        if (branch !== undefined) onSelectBranch(branch);
    };

    let body: React.ReactNode;
    if (error) body = <ErrorBanner message={error} />;
    else if (!table) body = <Skeleton className="h-96" />;
    // An empty result can arrive without columns, so check rows before splitting on the flag.
    else if (table.rows.length === 0) body = <EmptyMessage>No ledger entries for the selected filters.</EmptyMessage>;
    else {
        const { bodyTable, grandTotalTable } = toRollupDataTables(table, config.columnMetadata, {
            rollupFlagColumns: [branches.pnlByBranch.rollupFlag],
        });
        body =
            bodyTable.rows.length === 0 ? (
                <EmptyMessage>No ledger entries for the selected filters.</EmptyMessage>
            ) : (
                <DataGrid
                    data={bodyTable}
                    grandTotals={{ position: "bottom", data: grandTotalTable }}
                    defaultSort={[{ columnId: "NetProfit", direction: "desc" }]}
                    // Column filters would hide rows without updating the provided total.
                    capabilities={{ disableFiltering: true }}
                    theme={theme}
                    chromeless
                    onInteraction={handleInteraction}
                />
            );
    }

    return (
        <section
            aria-busy={isLoading}
            className={cn("flex flex-col gap-3 rounded-xl border bg-card p-4 transition-opacity", isLoading && table && "opacity-60", className)}
        >
            <header>
                <h2 className="font-visual-title text-visual-title text-emphasis-foreground">Profit and loss by branch</h2>
                <p className="text-200 text-muted-foreground">General ledger, GHS. Select a branch to filter the page.</p>
            </header>
            {body}
        </section>
    );
}
