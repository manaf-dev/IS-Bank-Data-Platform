import { useState } from "react";
import type { QueryTable } from "@microsoft/rayfin-connector-fabric-semanticmodel";
import { useSemanticModelQuery } from "@/hooks/use-semantic-model-query";

export interface LatestTableState {
    /** The latest successful result, kept while a replacement query loads. */
    table: QueryTable | null;
    isLoading: boolean;
    error: string | null;
}

/**
 * Runs a semantic model query and keeps the last successful table on screen while a
 * filter change re-queries, so visuals don't flash back to skeletons on every click.
 */
export function useLatestTable(connection: string, query: string): LatestTableState {
    const { data, isLoading, error } = useSemanticModelQuery({ connection, query });
    const current = data?.status === "success" ? data.table : null;
    const [last, setLast] = useState<QueryTable | null>(null);
    // Remember the newest success; React allows this render-time update for derived state.
    if (current !== null && current !== last) setLast(current);
    const failed = data?.status === "error" ? (data.error.message ?? error ?? "Query failed") : null;
    return { table: current ?? last, isLoading, error: failed };
}
