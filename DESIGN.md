# LLM Photo Organizer design

This local tool helps someone review tens of thousands of photos and videos without losing track of what has been scanned, classified, approved, or moved. The first screen should put the folder choice, current counts, and date queue within reach. File movement remains an explicit last step.

## Reading order

1. Identify the selected drive root and engine status.
2. Choose one folder and start a scan.
3. Set a home region if GPS-aware travel suggestions are wanted.
4. See library totals and job progress.
5. Narrow the date queue, open one date, and page through its files.
6. Request AI classification for a date or the unclassified date queue. Show the representative sample count.
7. Edit and save the date category, check the proposed path, approve it, then separately execute a confirmed move.

## Visual rules

- Use a quiet neutral canvas, dark text, hairline boundaries, and one accent for the primary action. Color always appears with a text status.
- Let the review queue carry the page. Keep orientation copy brief; avoid decorative hero art, all-caps eyebrow labels, fake photo illustrations, and repeated card decoration.
- Align peer counts and use tabular numerals. Keep paths and technical identifiers in a monospace face; keep ordinary Korean UI text in a legible sans-serif face.
- Use spacing and typography for hierarchy before adding a surface, border, or badge. Reserve panels for distinct actions such as source selection and final move approval.
- Keep touch controls at least 44 px tall on narrow screens, inputs at least 16 px, and visible focus rings for keyboard use.
- Show empty, loading, running, completed, and error states with a clear next action. Status messages must state whether any files moved.
- Keep the date and file page limits visible. Long filenames may truncate visually but remain available through their full title.
- Show GPS beside each file and the day-level inferred place beside its category. Make it clear that a sample may not represent every file in a date. Never imply that an away-from-home coordinate alone proves travel.

## Review checks

Inspect the first viewport and the active date at narrow and wide sizes. Verify keyboard navigation, focus, long filenames, large counts, empty filters, and the separate approve and move actions. Use temporary sample media for checks.

References: [Vercel design.md](https://vercel.com/design.md) for task-led hierarchy and visual restraint; [Vercel Web Interface Guidelines](https://vercel.com/design/guidelines) for interaction, accessibility, and performance. These are design references, not Vercel branding for this product.
