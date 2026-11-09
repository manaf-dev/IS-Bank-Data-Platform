import { describe, expect, it } from "vitest";
import type { InteractionEvent } from "@microsoft/fabric-visuals-core";
import { selectedValue } from "@/lib/selection";

const select = (fieldId: string, value: string): InteractionEvent => ({
    action: "select",
    selections: [
        {
            predicates: [
                { type: "set", fieldId, values: [{ kind: "value", value }] },
                { type: "set", fieldId: "Deposits", values: [{ kind: "value", value: 19547824.52 }] },
            ],
        },
    ],
});

describe("selectedValue", () => {
    it("returns the clicked value of the requested field", () => {
        expect(selectedValue([select("Region", "Ashanti")], "Region")).toBe("Ashanti");
    });

    it("returns null when the selection clears", () => {
        expect(selectedValue([{ action: "clear" }], "Region")).toBeNull();
    });

    it("lets the last event win", () => {
        expect(selectedValue([select("Region", "Ashanti"), { action: "clear" }], "Region")).toBeNull();
        expect(selectedValue([{ action: "clear" }, select("Region", "Volta")], "Region")).toBe("Volta");
    });

    it("returns null when the field isn't in the selection", () => {
        expect(selectedValue([select("Branch", "SUNYANI")], "Region")).toBeNull();
    });

    it("returns undefined when there are no events", () => {
        expect(selectedValue([], "Region")).toBeUndefined();
    });
});
