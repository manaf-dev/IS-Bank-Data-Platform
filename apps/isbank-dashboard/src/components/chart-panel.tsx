import type { VisualizationSpec } from "@microsoft/fabric-visuals";
import { VegaVisual } from "@microsoft/fabric-visuals";
import type { InteractionEvent } from "@microsoft/fabric-visuals-core";
import { useThemeContext } from "@/hooks/theme.context";
import { useLatestTable } from "@/hooks/use-latest-table";
import { type ColumnMetadataMap, toDataTable } from "@/lib/to-data-table";
import { selectedValue } from "@/lib/selection";
import { cn } from "@/lib/utils";
import { EmptyMessage, ErrorBanner, Skeleton } from "./states";

export interface ChartConfig {
    connection: string;
    query: string;
    columnMetadata: ColumnMetadataMap;
    vegaLiteSpec: VisualizationSpec;
}

interface ChartPanelProps {
    title: string;
    subtitle?: string;
    config: ChartConfig;
    height: number;
    className?: string;
    /** Column alias whose clicked value is reported through onSelect. */
    selectField?: string;
    onSelect?: (value: string | null) => void;
}

export function ChartPanel({ title, subtitle, config, height, className, selectField, onSelect }: ChartPanelProps) {
    const { theme } = useThemeContext();
    const { table, isLoading, error } = useLatestTable(config.connection, config.query);

    const handleInteraction = (events: InteractionEvent[]) => {
        if (!selectField || !onSelect) return;
        const value = selectedValue(events, selectField);
        if (value !== undefined) onSelect(value);
    };

    let body: React.ReactNode;
    if (error) body = <ErrorBanner message={error} />;
    else if (!table) body = <Skeleton className="h-full" />;
    else if (table.rows.length === 0) body = <EmptyMessage>No data for the selected filters.</EmptyMessage>;
    else
        body = (
            <VegaVisual
                spec={config.vegaLiteSpec}
                data={toDataTable(table, config.columnMetadata)}
                theme={theme}
                chromeless
                style={{ height }}
                onInteraction={handleInteraction}
            />
        );

    return (
        <section
            aria-busy={isLoading}
            className={cn("flex flex-col gap-3 rounded-xl border bg-card p-4 transition-opacity", isLoading && table && "opacity-60", className)}
        >
            <header>
                <h2 className="font-visual-title text-visual-title text-emphasis-foreground">{title}</h2>
                {subtitle && <p className="text-200 text-muted-foreground">{subtitle}</p>}
            </header>
            <div style={{ minHeight: height }}>{body}</div>
        </section>
    );
}
