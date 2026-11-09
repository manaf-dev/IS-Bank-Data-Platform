import { ChartPanel } from "@/components/chart-panel";
import { type KpiCardDef, KpiRow } from "@/components/kpi-row";
import { PnlGrid } from "@/components/pnl-grid";
import { branches } from "@/queries";
import type { PageProps } from "./executive-page";

const CARDS: KpiCardDef[] = [
    { field: "Income", label: "Income", kind: "money", note: "Interest and fees" },
    { field: "Expense", label: "Expense", kind: "money", note: "Operating costs" },
    { field: "NetProfit", label: "Net profit", kind: "money", note: "Income less expenses" },
    { field: "CostToIncome", label: "Cost-to-income", kind: "percent", note: "Lower is better" },
    { field: "Breaks", label: "GL vs core breaks", kind: "count", note: "Months fee income disagrees" },
];

export function BranchesPage({ filters, setFilters }: PageProps) {
    const selectBranch = (branch: string | null) => setFilters((f) => ({ ...f, branch }));
    return (
        <>
            <KpiRow config={branches.kpis.branchKpis(filters)} cards={CARDS} />
            <div className="grid grid-cols-1 gap-grid lg:grid-cols-5">
                <PnlGrid className="lg:col-span-3 lg:row-span-2" filters={filters} onSelectBranch={selectBranch} />
                <ChartPanel
                    className="lg:col-span-2"
                    title="Net profit by month"
                    subtitle="GHS; dashed line marks break-even"
                    height={240}
                    config={branches.profitTrend.profitTrend(filters)}
                />
                <ChartPanel
                    className="lg:col-span-2"
                    title="GL vs core fee breaks by branch"
                    subtitle="Months where the ledger and core banking disagree"
                    height={300}
                    config={branches.breaksByBranch.breaksByBranch(filters)}
                    selectField="Branch"
                    onSelect={selectBranch}
                />
            </div>
        </>
    );
}
