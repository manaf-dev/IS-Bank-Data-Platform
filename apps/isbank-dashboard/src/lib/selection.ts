import type { InteractionEvent } from "@microsoft/fabric-visuals-core";

/** The clicked value of one field from a select event, or null when the selection clears. */
export function selectedValue(events: InteractionEvent[], field: string): string | null | undefined {
    let result: string | null | undefined;
    for (const event of events) {
        if (event.action === "clear") {
            result = null;
            continue;
        }
        const predicate = event.selections[0]?.predicates.find((p) => p.type === "set" && p.fieldId === field);
        const first = predicate?.type === "set" ? predicate.values[0] : undefined;
        result = first?.kind === "value" ? String(first.value) : null;
    }
    return result;
}
