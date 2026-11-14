# CareerCoach AI global design system

This file and `frontend/css/variables.css` are the single source of truth for visual decisions. The attached approved **CareerCoach AI Landing Page Hero** image is the primary visual identity reference: editorial serif display type, deep navy text, vivid violet accents, warm pale backgrounds, soft dimensional lighting, and selective translucent surfaces. The image establishes the product's visual family; it does not authorize copying its hero composition onto internal pages.

Before implementing a page or section, read this guide, inspect `variables.css`, and inspect the shared components. Reuse existing tokens and components. Page-specific CSS may compose the visual language for the approved page reference, but must not define an independent palette, type, spacing, or component system. Record reusable decisions here and in the centralized tokens after they are approved. Do not change approved global tokens without explicit permission.

This is a system foundation, not an implemented homepage. Many component patterns below are conventions for future work; they are not already built as components.

## Visual principles

- Premium, polished, and readable; expressive artwork belongs where a reference calls for it.
- Pair editorial serif display moments with clear sans-serif interface typography.
- Balance warm neutral surfaces with violet identity accents. Do not tint every surface purple or pink.
- Use depth, gradient, glow, and glass selectively. Keep information-heavy internal screens calmer and easier to scan.
- Give every page a composition suited to its purpose while preserving common color, typography, spacing, component, and interaction conventions.

## Official palette

All production color values must be represented by semantic CSS custom properties in `variables.css`.

| Token | Hex / value | Role |
| --- | --- | --- |
| `--color-primary` | `#7028F5` | Brand violet, primary actions |
| `--color-primary-hover` | `#5B18D4` | Primary action hover |
| `--color-violet-secondary` | `#A34DFF` | Secondary violet accent |
| `--color-purple-deep` | `#4B1CA8` | Deep purple accent and gradient endpoint |
| `--color-lavender` | `#EAD8FF` | Lavender accent surface |
| `--color-lavender-pale` | `#F5EDFF` | Pale lavender surface |
| `--color-blush` | `#FBE5F3` | Blush accent |
| `--color-peach` | `#FFEADC` | Peach accent |
| `--color-ivory` | `#FFFAF2` | Warm ivory accent |
| `--color-bg` | `#FFF9F8` | Default warm page canvas |
| `--color-bg-secondary` | `#F8F1FF` | Secondary atmospheric background |
| `--color-surface` | `#FFFFFF` | Main component surface |
| `--color-surface-muted` | `#FCF8FF` | Quiet, lightly tinted surface |
| `--color-text` | `#101D64` | Deep navy primary text |
| `--color-text-secondary` | `#39466F` | Secondary readable text |
| `--color-text-muted` | `#68718F` | Supporting text, placeholder, metadata |
| `--color-on-primary` | `#FFFFFF` | Text and icons on primary fills |
| `--color-border` | `#E6DBF2` | Standard borders and dividers |
| `--color-border-strong` | `#C9A8F5` | Emphasized borders |
| `--color-success` | `#287A58` | Success and positive status |
| `--color-warning` | `#9A6413` | Warning status |
| `--color-error` | `#B33D46` | Error status |
| `--color-info` | `#3268A8` | Informational status |

Choose semantic tokens, not raw hex values in component or page rules. If a role is missing, add a named token and document it rather than approximating with an unrelated color. Status colors are functional states, not decorative accents.

## Backgrounds, gradients, and depth

- Use `--color-bg` for the default canvas, `--color-bg-secondary` for a section or ambient background, and `--color-surface` for content controls and panels.
- `--gradient-primary` (`linear-gradient(115deg, #8D23ED 0%, #7028F5 48%, #4B1CA8 100%)`) is for high-emphasis actions and selected accents.
- `--gradient-accent` (`linear-gradient(110deg, #7044FF 0%, #CF39A6 100%)`) is a limited expressive accent, not a general text or surface fill.
- `--gradient-atmosphere` (`linear-gradient(115deg, #FFF7F5 0%, #F9EFFF 54%, #E8DCFF 100%)`) is a soft background treatment.
- `--glass-bg` and `--glass-bg-strong` are translucent white layers. `--glass-border` and `--glass-blur` provide the matching edge and blur. Use glass for a small number of floating or contextual surfaces; avoid putting every card on glass.
- `--shadow-sm`, `--shadow-md`, and `--shadow-lg` indicate increasing elevation. `--shadow-glow` is reserved for restrained violet emphasis. Pair elevation with a clear layering purpose.
- Highlights and reflections may be used in reference-led artwork. Keep them out of routine controls and dense data surfaces unless the page reference asks for them.

## Typography

No external font request is required at runtime. The selected local/system stacks are:

- Editorial display: `--font-display`: Georgia, Times New Roman, serif.
- Interface headings: `--font-heading`: Segoe UI, Arial, sans-serif.
- Body and controls: `--font-body`: Segoe UI, Arial, sans-serif.

Use the display serif for major editorial headings and selected section titles. Use the sans-serif family for navigation, body copy, labels, controls, metadata, and dense application content. Italics may emphasize a phrase within a display heading when the reference calls for it; do not italicize all headings.

| Role | Token | Weight / line-height |
| --- | --- | --- |
| Display / large hero title | `--text-display` (`clamp(2.5rem, 5vw, 4.5rem)`) | Regular; `--line-tight` |
| Page title | `--text-3xl` (2.25rem) or display scale when composition allows | Regular or semibold by family; tight |
| Section title | `--text-2xl` to `--text-3xl` | Regular serif or semibold sans; heading line |
| Subheading | `--text-xl` (1.375rem) | Semibold; heading line |
| Body | `--text-base` (1rem) | Regular; `--line-normal` (1.55) |
| Lead/supporting body | `--text-lg` (1.125rem) | Regular; normal |
| Form label / button / navigation | `--text-label` or `--text-sm` (.875rem) | Medium or semibold |
| Metadata | `--text-meta` (.75rem) | Regular; muted text color |

Shared weights are `--weight-regular` (400), `--weight-medium` (500), `--weight-semibold` (600), and `--weight-bold` (700). Use `--tracking-tight` (-.025em) for display headings and `--tracking-normal` for interface text. Avoid using weight or size alone to communicate meaning.

## CSS variable conventions

Use names by role: `--color-*` for color, `--font-*` for font family, `--weight-*` for weight, `--text-*` for type size, `--line-*` for line-height, `--space-*` for spacing, `--radius-*` for corners, `--shadow-*` for elevation, `--gradient-*` for gradients, and `--transition-*` for motion. Semantic aliases should describe usage rather than a component's page-specific appearance. Keep literal color, radius, shadow, and spacing values inside the token file except for one-off geometry that has no visual-token role.

## Spacing and layout

The spacing scale is `--space-1` (.25rem), `--space-2` (.5rem), `--space-3` (.75rem), `--space-4` (1rem), `--space-5` (1.25rem), `--space-6` (1.5rem), `--space-8` (2rem), `--space-10` (2.5rem), `--space-12` (3rem), `--space-16` (4rem), and `--space-20` (5rem). Use `--grid-gap` for responsive grid spacing and `--section-space` for large section rhythm.

The main content maximum is `--container-max` (82rem), reading measure is `--container-reading` (48rem), and horizontal page padding uses `--page-gutter` (`clamp(1.25rem, 5.5vw, 5.5rem)`). Use the shared `.page-container` where its width and vertical rhythm fit. Align page content to a consistent container edge. Use grid and flex layouts according to the purpose of the page; do not force the homepage's split composition onto internal pages.

## Shared component conventions

The current implementation includes a shared site shell, `.page-container`, `.button`, `.button-primary`, `.button-secondary`, `.input`, `.card`, and `.surface-glass`. The conventions below guide future reusable components; do not create a large component library ahead of actual page needs.

| Component | Shared visual and interaction convention |
| --- | --- |
| Primary button | Pill shape, minimum 2.875rem height, semibold text, `--gradient-primary`, clear hover and focus; use for the main action. |
| Secondary button | Pill shape and same sizing as primary, violet outline and restrained surface fill; use for lower emphasis. |
| Navigation | Shared shell, deep navy labels, violet hover/current state, underline current page, generous spacing, visible focus. |
| Card / panel | White or muted neutral surface, shared border, `--radius-md` or `--radius-lg`, subtle elevation; use when grouping improves hierarchy. |
| Glass surface | Translucent white, fine light border, blur and medium elevation; use sparingly for floating/contextual content. |
| Form input | Full width, touch-usable minimum height, white surface, shared border and radius; clear label, placeholder, focus and validation states. |
| Dropdown | Match input height, border, radius, text, focus ring, and menu surface; preserve native keyboard behavior or provide equivalent accessible operation. |
| Tabs | Use a clear selected state in violet and a visible keyboard focus; avoid pills for every tab unless the content warrants it. |
| Badge | Compact label with readable contrast and restrained tinted background; status meaning uses semantic status tokens. |
| Tooltip | Small high-contrast surface, short text, keyboard-accessible trigger, and no essential information available only on hover. |
| Modal | Focus-managed dialog with clear title, close control, surface treatment, and restrained shadow; keep background content visually subordinate. |
| Progress indicator | Use violet for progress, neutral track, and a text equivalent for assistive technology. Do not communicate progress by color alone. |
| Alert | Use a semantic status token with a pale surface and icon or text label; keep the message readable and actionable. |
| Table | Clear column labels, modest dividers, aligned values, and responsive overflow/stacking appropriate to its data. Avoid decorative glass on dense data. |
| Empty / loading state | Calm surface and concise explanation; loading must have an accessible status. Avoid fake user or AI results. |
| Page header / breadcrumb | Align to the shared content container; maintain clear title hierarchy and predictable navigation semantics. |

Do not implement these components preemptively unless a requested page needs them. Reuse a shared primitive once it exists rather than copying its CSS into page styles.

## Current shared CSS foundations

- `components.css`: site header/nav/footer, buttons, input, card, and glass surface.
- `layout.css`: `.page-container` and shared footer alignment.
- `typography.css`: heading, paragraph, label, and metadata hierarchy.
- `global.css`: page canvas and keyboard focus/skip link.
- `responsive.css`: small-screen navigation, narrower page rhythm, and reduced-motion support.

Page-specific CSS belongs in `frontend/css/pages/`; it can compose shared tokens but must not redefine the palette or shared primitive appearance.

## Iconography and illustration

Use one coherent family within each experience. Prefer Lucide-style line icons or custom/local SVG with `viewBox`, consistent size and stroke, and `currentColor` where appropriate. Do not mix filled emoji, unrelated line sets, and mismatched stroke weights. Decorative SVGs should be hidden from assistive technology; meaningful icons need an accessible name or adjacent text. Use custom SVG illustration for reference-led product artwork. Keep illustration lighting dimensional and soft when used, while internal diagrams remain clear at small sizes. Do not add an icon package or font solely to decorate a page.

## Motion and interaction

Motion tokens: `--transition-fast` (160ms ease), `--transition-standard` (240ms ease), and `--transition-gentle` (360ms ease). Use short, restrained transitions for hover, focus, selection, and disclosure. Buttons may lift by a subtle amount; avoid large movement, flashing, parallax, or blocking page transitions. Scroll reveals and loading animations are optional and should only be added for a concrete purpose. Respect `prefers-reduced-motion`, preserve immediate access to content, and never make animation the only indicator of state.

Keyboard focus uses `--focus-ring` and must remain plainly visible. Controls need usable hit targets, semantic elements, labels, and discernible states for hover, focus, active, disabled, error, and success where relevant.

## Responsive rules

Breakpoints are mobile at 30rem, tablet at 48rem, desktop at 64rem, and wide at 90rem (`--breakpoint-mobile`, `--breakpoint-tablet`, `--breakpoint-desktop`, `--breakpoint-wide`). Media queries use the corresponding literal values because CSS custom properties are not broadly supported in media conditions. Build fluidly between breakpoints using clamp-based tokens and flexible grids.

- Desktop and laptop: use the shared content width and deliberate column proportions; preserve readable line lengths.
- Tablet: reduce gaps and section spacing; collapse multi-column content when columns become cramped.
- Mobile: stack content in a meaningful reading order, avoid horizontal overflow, keep navigation usable, and preserve touch-sized controls.
- Scale headings with the provided clamp tokens; keep SVGs fluid without distortion.
- Check each page at desktop, laptop, tablet, and narrow mobile widths. Do not make global responsive changes that break approved pages.

## Accessibility and contrast

Use semantic landmarks and heading order, descriptive link/action names, labels for inputs, and `aria-current` for the active route. Maintain visible keyboard focus and reduced-motion support. Ensure text and controls retain sufficient contrast against their surfaces; test gradient buttons at the lightest point behind their label. Do not rely on color alone to express status, progress, selection, or errors. Give decorative visuals empty alternative text or hide them from assistive technology; describe meaningful images concisely.

## Correct and incorrect usage

**Correct:** use `var(--color-primary)` for a new primary action, `var(--space-6)` for a shared card inset, the shared `.button-primary`, and a page composition that follows its own supplied reference. Use a calm neutral panel for dense dashboard content and reserve glass/glow for a purposeful focal surface.

**Incorrect:** add a page-local `#7b35ff` because it looks close, create a new type scale or custom button for one page, make every panel glass with a glow, use the homepage hero layout for onboarding, or invent illustrative data and present it as real.

## Change control

For each new page or section task, read this file and inspect `variables.css` and relevant shared components first. Implement only the requested composition. When a design decision is approved, update this document and the matching centralized token or shared component. Never silently change approved global tokens or already-approved sections. The homepage hero remains unimplemented until the user approves its implementation task.
