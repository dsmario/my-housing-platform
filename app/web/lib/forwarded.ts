// Behind the AWS load balancer, the last x-forwarded-for entry is the address it
// saw. Earlier entries come from the browser and can't be trusted.
export function lastForwardedIp(header: string | null): string | undefined {
  const forwarded = header?.split(",").map((s) => s.trim()).filter(Boolean) ?? [];
  return forwarded.length ? forwarded[forwarded.length - 1] : undefined;
}
