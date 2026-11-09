export type KpiKind = "count" | "money" | "percent";

const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const whole = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const precise = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export interface FormattedKpi {
    /** Short text for the card, e.g. "92.3M". */
    display: string;
    /** Unit shown beside the value, e.g. "GHS". */
    unit: string | null;
    /** The exact figure, for the tooltip and screen readers. */
    exact: string;
}

/**
 * Formats a KPI for a narrow card: money in compact units so six cards fit one row,
 * with the exact value kept for hover. A missing value shows an em dash, never zero.
 */
export function formatKpi(value: number | null, kind: KpiKind): FormattedKpi {
    if (value === null || !Number.isFinite(value)) return { display: "—", unit: null, exact: "No data" };
    switch (kind) {
        case "money":
            return { display: compact.format(value), unit: "GHS", exact: `GHS ${precise.format(value)}` };
        case "percent": {
            const pct = `${(value * 100).toFixed(1)}%`;
            return { display: pct, unit: null, exact: `${(value * 100).toFixed(2)}%` };
        }
        default:
            return { display: whole.format(value), unit: null, exact: whole.format(value) };
    }
}
