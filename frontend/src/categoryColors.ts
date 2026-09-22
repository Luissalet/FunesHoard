const MAP: Record<string, string> = {
  Coding: "var(--cat-coding)",
  Writing: "var(--cat-writing)",
  Reading: "var(--cat-reading)",
  Communication: "var(--cat-communication)",
  Meetings: "var(--cat-meetings)",
  Browsing: "var(--cat-browsing)",
  Design: "var(--cat-design)",
  Media: "var(--cat-media)",
  Games: "var(--cat-games)",
  System: "var(--cat-system)",
  Other: "var(--cat-other)",
};

export function categoryColor(category: string): string {
  return MAP[category] || MAP.Other;
}
