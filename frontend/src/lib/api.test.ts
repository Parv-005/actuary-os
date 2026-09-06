import { describe, expect, it } from "vitest";
import { dimLabel, formatPct, isPolling, stageLabel } from "./api";
import { TERMINAL_STATUSES } from "./api";

describe("isPolling", () => {
  it("polls while the pipeline runs, stops on gates and terminals", () => {
    for (const s of [
      "INGESTING",
      "VALIDATING",
      "ANALYZED",
      "INVESTIGATING",
      "REPORTING",
      "QA",
    ]) {
      expect(isPolling(s)).toBe(true);
    }
    for (const s of [
      "WAITING_FOR_HUMAN",
      "BLOCKED",
      "FAILED",
      "COMPLETED",
      "REJECTED",
      "APPROVED",
    ]) {
      expect(isPolling(s)).toBe(false);
    }
  });
});

describe("TERMINAL_STATUSES", () => {
  it("covers the backend TERMINAL set", () => {
    expect(TERMINAL_STATUSES).toEqual(
      new Set(["COMPLETED", "REJECTED", "CANCELLED"])
    );
  });
});

describe("stageLabel", () => {
  it("labels known stages, passes through unknown ones", () => {
    expect(stageLabel("data_prep")).toBe("Data Prep");
    expect(stageLabel("qa")).toBe("QA");
    expect(stageLabel("mystery")).toBe("mystery");
  });
});

describe("formatPct", () => {
  it("formats ratio values as percent strings", () => {
    expect(formatPct(0.673)).toBe("67.3%");
    expect(formatPct(null)).toBe("—");
    expect(formatPct(undefined)).toBe("—");
  });
});

describe("dimLabel", () => {
  it("joins dimension values, portfolio fallback", () => {
    expect(dimLabel({})).toBe("Portfolio");
    expect(
      dimLabel({
        product: "Commercial",
        segment: "Construction",
        region: "South",
      })
    ).toBe("Commercial · Construction · South");
  });
});
