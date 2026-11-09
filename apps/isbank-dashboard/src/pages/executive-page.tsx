import { ChartPanel } from "@/components/chart-panel";
import { type KpiCardDef, KpiRow } from "@/components/kpi-row";
import type { DashboardFilters } from "@/lib/dashboard-filters";
import { executive } from "@/queries";

const CARDS: KpiCardDef[] = [
    { field: "Customers", label: "Customers", kind: "count", note: "All time; ignores filters" },
    { field: "ActiveCustomers", label: "Active customers", kind: "count", note: "Transacted in period" },
    { field: "Deposits", label: "Deposits", kind: "money", note: "Latest month-end" },
    { field: "LoanPrincipal", label: "Loan book", kind: "money", note: "Ignores period" },
    { field: "NplRatio", label: "NPL ratio", kind: "percent", note: "Arrears and default" },
    { field: "NetProfit", label: "Net profit", kind: "money", note: "Income less expenses" },
];

interface PageProps {
    filters: DashboardFilters;
    setFilters: React.Dispatch<React.SetStateAction<DashboardFilters>>;
}

export function ExecutivePage({ filters, setFilters }: PageProps) {
    return (
        <>
            <KpiRow config={executive.kpis.executiveKpis(filters)} cards={CARDS} />
            <div className="grid grid-cols-1 gap-grid lg:grid-cols-3">
                <ChartPanel
                    className="lg:col-span-2"
                    title="Transaction value by month"
                    subtitle="GHS"
                    height={300}
                    config={executive.transactionTrend.transactionTrend(filters)}
                />
                <ChartPanel
                    title="Deposits by region"
                    subtitle="Latest month-end balance, GHS"
                    height={300}
                    config={executive.depositsByRegion.depositsByRegion(filters)}
                    selectField="Region"
                    onSelect={(region) => setFilters((f) => ({ ...f, region, branch: null }))}
                />
                <ChartPanel
                    className="lg:col-span-2"
                    title="Net profit by branch"
                    subtitle="GL income less expenses, GHS"
                    height={380}
                    config={executive.profitByBranch.profitByBranch(filters)}
                    selectField="Branch"
                    onSelect={(branch) => setFilters((f) => ({ ...f, branch }))}
                />
                <ChartPanel
                    title="Transactions by channel"
                    subtitle="Count of transactions"
                    height={380}
                    config={executive.transactionsByChannel.transactionsByChannel(filters)}
                />
            </div>
        </>
    );
}

export type { PageProps };
