import { describe, expect, it } from "vitest";
import { NO_FILTERS, daxString, filterArguments, withFilters } from "@/lib/dashboard-filters";

const grouped = "EVALUATE\nSUMMARIZECOLUMNS ( 'dim_branch'[region], \"Deposits\", [Deposits] )\nORDER BY 'dim_branch'[region]\n";

describe("dashboard filters", () => {
    it("leaves the query untouched without filters", () => {
        expect(withFilters(grouped, NO_FILTERS)).toBe(grouped);
    });

    it("wraps the table in CALCULATETABLE and keeps ORDER BY outside", () => {
        const query = withFilters(grouped, { ...NO_FILTERS, year: 2024, region: "Greater Accra" });
        expect(query).toBe(
            "EVALUATE\nCALCULATETABLE (\nSUMMARIZECOLUMNS ( 'dim_branch'[region], \"Deposits\", [Deposits] ),\n" +
                "    TREATAS({2024}, 'dim_date'[year]),\n    TREATAS({\"Greater Accra\"}, 'dim_branch'[region])\n)\n" +
                "ORDER BY 'dim_branch'[region]\n",
        );
    });

    it("wraps a ROW query with no ORDER BY", () => {
        const query = withFilters('EVALUATE\nROW ( "Customers", [Customers] )\n', { ...NO_FILTERS, quarter: 3 });
        expect(query).toBe("EVALUATE\nCALCULATETABLE (\nROW ( \"Customers\", [Customers] ),\n    TREATAS({3}, 'dim_date'[quarter])\n)");
    });

    it("escapes quotes in text filters", () => {
        expect(daxString('A "B"')).toBe('"A ""B"""');
        expect(filterArguments({ ...NO_FILTERS, branch: 'X"Y' })).toEqual([`TREATAS({"X""Y"}, 'dim_branch'[branch_name])`]);
    });

    it("rejects a query it can't wrap", () => {
        expect(() => withFilters("DEFINE VAR x = 1 EVALUATE ROW(\"x\", x)", { ...NO_FILTERS, year: 2024 })).toThrow();
    });
});
