import { Moon, Sun } from "lucide-react";
import { useState } from "react";
import { ChartPanel } from "@/components/chart-panel";
import { FilterBar } from "@/components/filter-bar";
import { KpiRow } from "@/components/kpi-row";
import { useThemeContext } from "@/hooks/theme.context";
import { type DashboardFilters, NO_FILTERS } from "@/lib/dashboard-filters";
import { executive } from "@/queries";

function App() {
    const { isDark, toggleTheme } = useThemeContext();
    const [filters, setFilters] = useState<DashboardFilters>(NO_FILTERS);

    return (
        <div className="min-h-full bg-background">
            <header className="border-b-4 border-gold bg-brand text-on-brand">
                <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 md:px-6">
                    <div>
                        <p className="font-heading text-500 font-semibold leading-500">IS Bank</p>
                        <p className="text-200 tracking-wide text-gold">Data &amp; Analytics Platform</p>
                    </div>
                    <button
                        type="button"
                        onClick={toggleTheme}
                        aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
                        className="rounded-lg p-2 hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-gold"
                    >
                        {isDark ? <Sun aria-hidden className="icon-size-300" /> : <Moon aria-hidden className="icon-size-300" />}
                    </button>
                </div>
            </header>

            <main className="mx-auto flex max-w-7xl flex-col gap-grid px-4 py-5 md:px-6">
                <div>
                    <h1 className="font-page-title text-page-title text-emphasis-foreground">Executive summary</h1>
                    <p className="font-page-subtitle text-page-subtitle text-muted-foreground">
                        Headline performance across the bank's 15 branches. Click a region or branch bar to filter.
                    </p>
                </div>

                <FilterBar value={filters} onChange={setFilters} />
                <KpiRow filters={filters} />

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
            </main>
        </div>
    );
}

export default App;
