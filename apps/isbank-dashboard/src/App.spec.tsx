//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { readCssTheme } from "@microsoft/fabric-visuals-core";
import App from "@/App";
import { ThemeContext } from "@/hooks/theme.context";
import { clearCachedQueries } from "@/lib/query-cache";

// Offline client: the KPI query gets a row shaped like the live sm_isbank result,
// and every other query an empty table.
const mockExecuteQuery = vi.hoisted(() => vi.fn());

vi.mock("@/lib/rayfin-client", () => ({
    getRayfinClient: () => ({
        connectors: new Proxy({}, { get: () => ({ executeQuery: mockExecuteQuery }) }),
    }),
}));

const KPI_COLUMNS = ["[Customers]", "[Active Customers]", "[Deposits]", "[Loan Principal]", "[NPL Ratio]", "[Net Profit]"];

function renderApp() {
    return render(
        <ThemeContext.Provider value={{ isDark: false, toggleTheme: () => {}, theme: readCssTheme() }}>
            <App />
        </ThemeContext.Provider>,
    );
}

describe("App", () => {
    beforeEach(() => {
        clearCachedQueries();
        mockExecuteQuery.mockImplementation(async ({ query }: { query: string }) =>
            query.includes("ROW (")
                ? {
                      status: "success",
                      requestId: "kpi",
                      table: {
                          columns: KPI_COLUMNS.map((name) => ({ name, dataType: "Double" })),
                          rows: [[1749, 1578, 92285251.54, 8474650, 0.2305, 5784142.73]],
                      },
                  }
                : { status: "success", requestId: "other", table: { columns: [], rows: [] } },
        );
    });

    it("renders the Executive summary with its filters and KPI cards", async () => {
        renderApp();
        expect(screen.getByRole("heading", { name: "Executive summary" })).toBeInTheDocument();
        for (const label of ["Year", "Quarter", "Region", "Branch"]) {
            expect(screen.getByLabelText(label)).toBeInTheDocument();
        }
        expect(await screen.findByText("Net profit")).toBeInTheDocument();
        expect(screen.getByText("92.3M")).toBeInTheDocument();
        expect(screen.getByText("NPL ratio")).toBeInTheDocument();
    });

    it("shows an empty message for charts without rows", async () => {
        renderApp();
        expect((await screen.findAllByText("No data for the selected filters.")).length).toBe(4);
    });
});
