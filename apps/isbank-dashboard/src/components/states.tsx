import { AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";

export function Skeleton({ className }: { className?: string }) {
    return <div aria-hidden className={cn("animate-pulse rounded-lg bg-muted", className)} />;
}

export function ErrorBanner({ message }: { message: string }) {
    return (
        <div role="alert" className="flex items-start gap-2 rounded-lg bg-destructive p-3 text-300 text-destructive-foreground">
            <AlertTriangle aria-hidden className="mt-0.5 shrink-0 icon-size-200" />
            <span>{message}</span>
        </div>
    );
}

export function EmptyMessage({ children }: { children: React.ReactNode }) {
    return <div className="flex h-full min-h-24 items-center justify-center text-300 text-muted-foreground">{children}</div>;
}
