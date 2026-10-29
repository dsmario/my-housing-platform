import { describe, expect, it } from "vitest";
import { lastForwardedIp } from "./forwarded";

describe("lastForwardedIp: the youth's address for the audit log", () => {
  it("takes the last entry, the one the load balancer added", () => {
    expect(lastForwardedIp("1.2.3.4, 203.0.113.7")).toBe("203.0.113.7");
  });
  it("ignores spaces and empty entries", () => {
    expect(lastForwardedIp(" 203.0.113.7 , ")).toBe("203.0.113.7");
  });
  it("gives nothing without the header (local, no load balancer)", () => {
    expect(lastForwardedIp(null)).toBeUndefined();
    expect(lastForwardedIp("")).toBeUndefined();
  });
});
