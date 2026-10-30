// "2026-10-30 02:00 UTC": the same on every screen, and matches the AWS alarms.
export function utc(iso: string): string {
  return `${iso.slice(0, 16).replace("T", " ")} UTC`;
}

// "1 record", "5 records".
export function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}
