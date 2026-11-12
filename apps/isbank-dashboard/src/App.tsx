import { Moon, Sun } from "lucide-react";
import { useState } from "react";
import { FilterBar } from "@/components/filter-bar";
import { useThemeContext } from "@/hooks/theme.context";
import { type DashboardFilters, NO_FILTERS } from "@/lib/dashboard-filters";
import { cn } from "@/lib/utils";
import { BranchesPage } from "@/pages/branches-page";
import { ExecutivePage, type PageProps } from "@/pages/executive-page";

interface PageDef {
    id: string;
    tab: string;
    title: string;
    subtitle: string;
    Component: (props: PageProps) => React.ReactNode;
}

const PAGES: PageDef[] = [
    {
        id: "executive",
        tab: "Executive",
        title: "Executive summary",
        subtitle: "Headline performance across the bank's 15 branches. Click a region or branch bar to filter.",
        Component: ExecutivePage,
    },
    {
        id: "branches",
        tab: "Branches",
        title: "Branch performance",
        subtitle: "Profit and loss from the general ledger, and where it disagrees with core banking.",
        Component: BranchesPage,
    },
];

function App() {
    const { isDark, toggleTheme } = useThemeContext();
    // Filters are shared by every page, like synced slicers in the Power BI report.
    const [filters, setFilters] = useState<DashboardFilters>(NO_FILTERS);
    const [pageId, setPageId] = useState(PAGES[0].id);
    const page = PAGES.find((p) => p.id === pageId) ?? PAGES[0];

    return (
        <div className="min-h-full bg-background">
            <header className="border-b-4 border-gold bg-brand text-on-brand">
                <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 pt-3 md:px-6">
                    <div className="pb-3">
                        <p className="font-heading text-500 font-semibold leading-500">IS Bank</p>
                        <p className="text-200 tracking-wide text-gold">Data &amp; Analytics Platform</p>
                    </div>
                    <nav aria-label="Pages" className="order-last flex w-full gap-1 md:order-none md:w-auto md:self-end">
                        {PAGES.map((p) => (
                            <button
                                key={p.id}
                                type="button"
                                aria-current={p.id === page.id ? "page" : undefined}
                                onClick={() => setPageId(p.id)}
                                className={cn(
                                    "rounded-t-lg px-4 py-2 text-300 font-semibold focus-visible:outline-2 focus-visible:outline-gold",
                                    p.id === page.id ? "bg-gold text-on-gold" : "text-on-brand hover:bg-white/10",
                                )}
                            >
                                {p.tab}
                            </button>
                        ))}
                    </nav>
                    <button
                        type="button"
                        onClick={toggleTheme}
                        aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
                        className="mb-3 rounded-lg p-2 hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-gold"
                    >
                        {isDark ? <Sun aria-hidden className="icon-size-300" /> : <Moon aria-hidden className="icon-size-300" />}
                    </button>
                </div>
            </header>

            <main className="mx-auto flex max-w-7xl flex-col gap-grid px-4 py-5 md:px-6">
                <div>
                    <h1 className="font-page-title text-page-title text-emphasis-foreground">{page.title}</h1>
                    <p className="font-page-subtitle text-page-subtitle text-muted-foreground">{page.subtitle}</p>
                </div>
                <FilterBar value={filters} onChange={setFilters} />
                <page.Component filters={filters} setFilters={setFilters} />
            </main>
        </div>
    );
}

export default App;
