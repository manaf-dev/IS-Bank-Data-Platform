import { RotateCcw } from "lucide-react";
import { useLatestTable } from "@/hooks/use-latest-table";
import { type DashboardFilters, NO_FILTERS } from "@/lib/dashboard-filters";
import { type ColumnMetadataMap, toDataTable } from "@/lib/to-data-table";
import { filters as filterQueries } from "@/queries";

interface FilterBarProps {
    value: DashboardFilters;
    onChange: (next: DashboardFilters) => void;
}

interface Option {
    value: string;
    label: string;
}

function Select({ label, value, options, onChange }: {
    label: string;
    value: string;
    options: Option[];
    onChange: (value: string) => void;
}) {
    return (
        <label className="flex min-w-36 flex-1 flex-col gap-1 text-200 font-medium text-muted-foreground">
            {label}
            <select
                value={value}
                onChange={(e) => onChange(e.target.value)}
                className="h-9 rounded-lg border bg-card px-2 text-300 text-foreground focus-visible:outline-2 focus-visible:outline-ring"
            >
                <option value="">All</option>
                {options.map((o) => (
                    <option key={o.value} value={o.value}>
                        {o.label}
                    </option>
                ))}
            </select>
        </label>
    );
}

/** Rows of a filter-option query as plain objects keyed by column alias. */
function useOptionRows(config: { connection: string; query: string; columnMetadata: ColumnMetadataMap }) {
    const { table } = useLatestTable(config.connection, config.query);
    if (!table) return [];
    const data = toDataTable(table, config.columnMetadata);
    return data.rows.map((row) => Object.fromEntries(data.columns.map((c, i) => [c.name, row[i]])));
}

export function FilterBar({ value, onChange }: FilterBarProps) {
    const periods = useOptionRows(filterQueries.periods.periodOptions());
    // The model's UNKNOWN branch member has no region or name, so it isn't offered.
    const branches = useOptionRows(filterQueries.branches.branchOptions()).filter((r) => r.Region && r.Branch);

    const years = [...new Set(periods.map((r) => Number(r.Year)))];
    const quarters = periods
        .filter((r) => value.year === null || r.Year === value.year)
        .reduce<Option[]>((acc, r) => {
            const q = String(r.Quarter);
            return acc.some((o) => o.value === q) ? acc : [...acc, { value: q, label: String(r.QuarterName) }];
        }, [])
        .sort((a, b) => Number(a.value) - Number(b.value));
    const regions = [...new Set(branches.map((r) => String(r.Region)))];
    const branchNames = branches
        .filter((r) => value.region === null || r.Region === value.region)
        .map((r) => String(r.Branch));

    const set = (patch: Partial<DashboardFilters>) => onChange({ ...value, ...patch });
    const active = Object.values(value).some((v) => v !== null);

    return (
        <div role="group" aria-label="Filters" className="flex flex-wrap items-end gap-3 rounded-xl border bg-card p-3">
            <Select
                label="Year"
                value={value.year?.toString() ?? ""}
                options={years.map((y) => ({ value: String(y), label: String(y) }))}
                onChange={(v) => set({ year: v ? Number(v) : null })}
            />
            <Select
                label="Quarter"
                value={value.quarter?.toString() ?? ""}
                options={quarters}
                onChange={(v) => set({ quarter: v ? Number(v) : null })}
            />
            <Select
                label="Region"
                value={value.region ?? ""}
                options={regions.map((r) => ({ value: r, label: r }))}
                onChange={(v) => {
                    const region = v || null;
                    // Keep the branch only if it belongs to the new region.
                    const keep = branches.some((r) => r.Branch === value.branch && (region === null || r.Region === region));
                    set({ region, branch: keep ? value.branch : null });
                }}
            />
            <Select
                label="Branch"
                value={value.branch ?? ""}
                options={branchNames.map((b) => ({ value: b, label: b }))}
                onChange={(v) => set({ branch: v || null })}
            />
            <button
                type="button"
                onClick={() => onChange(NO_FILTERS)}
                disabled={!active}
                className="flex h-9 items-center gap-1.5 rounded-lg border px-3 text-300 font-medium text-foreground hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-50"
            >
                <RotateCcw aria-hidden className="icon-size-200" />
                Reset
            </button>
        </div>
    );
}
