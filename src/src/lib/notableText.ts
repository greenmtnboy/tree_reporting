// A notable tree's text, as a report proposes it
// (docs/NAMED_TREES_AND_CROWN_POSITION.md). The Firestore rules
// (terraform/bootstrap/rules/firestore.rules), the reviewer
// (reviewer/modifications.ts) and data/raw/tree_modification_info.py hold the
// same limits.
export const NOTABLE_TEXT_LIMITS = {
  proposedTreeName: 60,
  proposedSignificance: 500,
  proposedSignificanceSource: 300,
} as const
export type NotableTextField = keyof typeof NOTABLE_TEXT_LIMITS

/** Whitespace collapsed and capped; null when nothing is left. */
export function notableText(value: string | null | undefined, field: NotableTextField): string | null {
  if (typeof value !== 'string') return null
  return value.split(/\s+/).filter(Boolean).join(' ').slice(0, NOTABLE_TEXT_LIMITS[field]).trim() || null
}
