import { describe, expect, it } from "vitest";
import { formatKpi } from "@/lib/kpi-format";

describe("formatKpi", () => {
    it("shows money in compact GHS with the exact figure kept", () => {
        expect(formatKpi(92285251.54, "money")).toEqual({ display: "92.3M", unit: "GHS", exact: "GHS 92,285,251.54" });
        expect(formatKpi(-120185.6, "money").display).toBe("-120.2K");
    });

    it("shows counts in full with grouping", () => {
        expect(formatKpi(1749, "count")).toEqual({ display: "1,749", unit: null, exact: "1,749" });
    });

    it("shows ratios as percentages", () => {
        expect(formatKpi(0.23052828, "percent")).toEqual({ display: "23.1%", unit: null, exact: "23.05%" });
    });

    it("keeps a real zero and shows a dash for missing data", () => {
        expect(formatKpi(0, "money").display).toBe("0");
        expect(formatKpi(null, "count")).toEqual({ display: "—", unit: null, exact: "No data" });
    });
});
